"""Fictional bounded file-policy adapters: actual local HTTP/archive bytes, no sends or legal acceptance."""
from __future__ import annotations

import io
import json
import threading
import zipfile
from datetime import datetime, timezone

import pytest

import case_notes
import client_file
import client_file_policy as policy
import clock
import engagement
import purge
from test_cloud_client_file_policy import case, approve, applicable, destructive, destruction, ACTOR  # noqa: F401
from test_prospects import firm, server, call, ok  # noqa: F401
from test_staff_consent_http import client

# Imported pytest fixtures deliberately share names with the fixture arguments.
# ruff: noqa: F811

RECIPIENT = {"name": "Fictional Recipient", "authority": "client", "authority_evidence": "Fictional current authority reviewed",
             "method": "in_person", "destination": "", "authority_reviewed": True}


def choices(inv):
    return [{"path": r["path"], "sha256": r["sha256"], "input_sha256": r["input_sha256"],
             "include": r["reviewable"], "reason": "Fictional attorney reviewed this exact material or arranged an alternative."}
            for r in inv["entries"]]


def ready(case):
    out = approve(case)
    inv = client_file.inventory(case, case.parent.parent / "portal")
    out = policy.approve_inventory(case, out["revision"], out["snapshot_sha256"], inv["snapshot_sha256"], choices(inv), **ACTOR)
    out = policy.save_recipient(case, out["revision"], out["snapshot_sha256"], RECIPIENT, **ACTOR)
    return policy.display(case)


def prepare(case):
    current = ready(case)
    out = engagement.export_file(case, case.parent, **ACTOR, expected_binding_sha256=current["handover"]["binding_sha256"])
    f = out["file"]
    return engagement.approve_file(case, f["sha256"], **ACTOR, expected_binding_sha256=f["binding_sha256"])


def end(case):
    rec = engagement.read(case)
    rec["end"] = {"state": "closed", "on": "2010-01-01", "by": ACTOR["who"], "role": "attorney", "at": clock.stamp(), "letter": None}
    (case / engagement.FILE).write_text(json.dumps(rec))


def scheduled(case):
    end(case)
    destruction(case)
    purge.record_contact(case, "in_person", "2026-10-05", "Fictional actual contact", **ACTOR)
    purge.review_originals(case, [], True, **ACTOR)
    return purge.ask(case, "Fictional approved disposition", **ACTOR, attorneys=2)


def test_no_selected_policy_never_prepares_default_archive(case):
    with pytest.raises(ValueError, match="jurisdiction"):
        engagement.export_file(case, case.parent, **ACTOR)
    assert not engagement.read(case).get("file")
    assert not engagement.exports_folder(case.parent).exists()


def test_exact_inventory_notes_and_protective_handover_with_active_hold(case):
    (case / "reviewed-work.txt").write_text("Fictional attorney work product")
    case_notes.add_note(case, "Fictional case advice", ACTOR["who"], "attorney", carried="unrelated-source-id", attorney_only=True)
    (case / "unknown.bin").write_bytes(b"unsupported fictional material")
    held = applicable() | {"holds": [{"kind": "preservation", "description": "Fictional preservation", "active": True}]}
    approve(case, held)
    inv = client_file.inventory(case, case.parent.parent / "portal")
    row = next(r for r in inv["entries"] if r["path"].endswith("notes.json"))
    assert row["category"] == "work_product_notes" and row["output_path"].endswith("CASE-NOTES.txt")
    assert next(r for r in inv["entries"] if r["path"].endswith("unknown.bin"))["category"] == "unsupported"
    out = policy.view(case)
    policy.approve_inventory(case, out["revision"], out["snapshot_sha256"], inv["snapshot_sha256"], choices(inv), **ACTOR)
    out = policy.view(case)
    policy.save_recipient(case, out["revision"], out["snapshot_sha256"], RECIPIENT, **ACTOR)
    out = policy.display(case)
    file = engagement.export_file(case, case.parent, **ACTOR, expected_binding_sha256=out["handover"]["binding_sha256"])["file"]
    with zipfile.ZipFile(engagement.file_path(case, case.parent)) as archive:
        assert archive.read(f"clients/{case.name}/reviewed-work.txt") == b"Fictional attorney work product"
        notes = archive.read(f"clients/{case.name}/CASE-NOTES.txt").decode()
        assert "Fictional case advice" in notes and "unrelated-source-id" not in notes and "attorney_only" not in notes
        manifest = archive.read("manifest.json").decode()
        assert "FL 88-11" not in manifest and "client_file_policy.json" not in manifest and "documents.json" not in manifest
        assert not any(n.endswith(("notes.json", "unknown.bin")) for n in archive.namelist())
    assert file["binding_current"] and file["integrity_current"]
    assert policy.view(case)["destruction"]["state"] == "held"


@pytest.mark.parametrize("relative", ["fact_graph_reviewed.json", "staff-upload-receipts/fictional.json", "staff-upload-receipts/fictional.txt",
                                      "source-authorization/private.pdf", "family-operations/private.txt"])
def test_registered_security_material_never_candidate(case, relative):
    path = case / relative
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text("Fictional security secret")
    data = json.loads((case / "documents.json").read_text())
    data["documents"] = [{"id": "fake", "source": str(path), "pages": [1]}]
    (case / "documents.json").write_text(json.dumps(data))
    selected, _, _ = client_file.gather(case, case.parent.parent / "portal")
    inv = client_file.inventory(case, case.parent.parent / "portal")
    assert path not in [r.source for r in selected]
    assert not any(r["path"].endswith(relative) for r in inv["entries"])


def test_source_folder_cannot_read_sibling_or_firm_data(case):
    sibling = case.parent / "unrelated-fictional-case"
    sibling.mkdir()
    (sibling / "other.txt").write_text("Other case private bytes")
    for root in (sibling, case.parent.parent):
        (case / "meta.json").write_text(json.dumps({"source_folder": str(root)}))
        with pytest.raises(ValueError, match="protected"):
            client_file.inventory(case, case.parent.parent / "portal")
    source = case / "source"
    source.mkdir()
    (source / "own.txt").write_text("Own fictional original")
    (case / "meta.json").write_text(json.dumps({"source_folder": str(source)}))
    assert any(r["path"].endswith("own.txt") for r in client_file.inventory(case, case.parent.parent / "portal")["entries"])


def test_changed_same_filename_invalidates_every_handover_action_preserving_history(case):
    path = case / "work.txt"
    path.write_text("first fictional work")
    out = prepare(case)
    f = out["file"]
    record = (case / engagement.FILE).read_bytes()
    history = policy.read(case)["history"]
    path.write_text("changed fictional work")
    assert policy.read(case)["history"] == history
    view = engagement.view(case, role="attorney")
    assert not view["file"]["binding_current"] and not view["file"]["approval_current"]
    for fn in (lambda: engagement.file_path(case, case.parent),
               lambda: engagement.approve_file(case, f["sha256"], **ACTOR, expected_binding_sha256=f["binding_sha256"]),
               lambda: engagement.file_returned(case, "2026-10-05", "in_person", **ACTOR, sha256=f["sha256"], receipt_reference="receipt",
                   expected_binding_sha256=f["binding_sha256"], recipient_sha256=f["binding"]["recipient_sha256"])):
        with pytest.raises(ValueError):
            fn()
    assert (case / engagement.FILE).read_bytes() == record


def test_corrupt_archive_holds_download_approval_and_truthful_view(case):
    f = prepare(case)["file"]
    path = engagement.file_path(case, case.parent)
    path.write_bytes(b"changed archive")
    view = engagement.view(case, role="attorney")["file"]
    assert view["binding_current"] and not view["integrity_current"] and not view["approval_current"] and view["integrity_hold"]
    with pytest.raises(ValueError, match="changed"):
        engagement.approve_file(case, f["sha256"], **ACTOR, expected_binding_sha256=f["binding_sha256"])


@pytest.mark.parametrize("date", ["2026-10-04", "2026-10-06"])
def test_delivery_receipt_rejects_future_and_preapproval_date(case, date):
    f = prepare(case)["file"]
    before = (case / engagement.FILE).read_bytes()
    with pytest.raises(ValueError, match="Actual delivery|future"):
        engagement.file_returned(case, date, "in_person", **ACTOR, sha256=f["sha256"], receipt_reference="Fictional receipt",
            expected_binding_sha256=f["binding_sha256"], recipient_sha256=f["binding"]["recipient_sha256"])
    assert (case / engagement.FILE).read_bytes() == before


def test_delivery_receipt_same_day_and_stale_recipient_preserves_actual_history(case):
    f = prepare(case)["file"]
    out = engagement.file_returned(case, "2026-10-05", "in_person", **ACTOR, sha256=f["sha256"], receipt_reference="Fictional actual acknowledgement",
        expected_binding_sha256=f["binding_sha256"], recipient_sha256=f["binding"]["recipient_sha256"])
    old = out["file"]["returned"]
    current = policy.view(case)
    policy.save_recipient(case, current["revision"], current["snapshot_sha256"], RECIPIENT | {"name": "Other Fictional Recipient"}, **ACTOR)
    out = engagement.view(case, role="attorney")
    assert not out["file"]["binding_current"] and out["file"]["returned"] == old


def test_operational_open_case_old_legal_approval_never_schedules(case):
    destruction(case)
    with pytest.raises(ValueError, match="Operationally close"):
        purge.ask(case, "Delete active case", **ACTOR)
    assert not purge.entry(case.parent, case.name)
    assert policy.view(case)["facts"]["representation_completed_on"] == "2010-01-01"


@pytest.mark.parametrize("change", ["hold", "evidence", "reopen"])
def test_waiting_purge_current_hold_evidence_and_reopen_deny_confirm_worker_and_submit(case, monkeypatch, change):
    scheduled(case)
    original = (case / "documents.json").read_bytes()
    if change == "hold":
        out = policy.view(case)
        policy.save_facts(case, out["revision"], out["facts"] | {"holds": [{"kind": "court_order", "description": "New fictional order", "active": True}]}, **ACTOR)
    elif change == "evidence":
        data = json.loads(original)
        data["new_fictional_source"] = True
        (case / "documents.json").write_text(json.dumps(data))
    else:
        rec = engagement.read(case)
        rec["end"] = None
        (case / engagement.FILE).write_text(json.dumps(rec))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    before = (case / "documents.json").read_bytes()
    with pytest.raises(purge.PurgeError):
        purge.confirm(case.parent, case.name, "Second Fictional Attorney", "attorney")
    # Even a retained waiting operation whose second-actor/wait gates are satisfied cannot bypass current policy.
    row = purge.entry(case.parent, case.name)
    row["confirmed_by"] = "Second Fictional Attorney"
    data = purge._purges(case.parent)
    data["cases"][case.name] = row
    purge._save_purges(case.parent, data)
    with pytest.raises(purge.PurgeError):
        purge.run(case.parent, case.name)
    with pytest.raises(purge.PurgeError):
        purge.submit(case.parent, case.name, ACTOR["who"])
    assert case.exists() and (case / "documents.json").read_bytes() == before


def test_partial_purge_missing_authority_holds_recovery_and_recreated_case(case, monkeypatch):
    scheduled(case)
    purge.confirm(case.parent, case.name, "Second Fictional Attorney", "attorney")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    def fail_after_effect(*args, **kwargs):
        (case / policy.FILE).unlink()
        (case / "documents.json").unlink()
        raise OSError("Fictional interrupted destruction")
    monkeypatch.setattr(purge, "empty_stores", fail_after_effect)
    with pytest.raises(OSError, match="interrupted"):
        purge.run(case.parent, case.name)
    central = purge.entry(case.parent, case.name)
    with pytest.raises(purge.PurgeError, match="incident recovery"):
        purge.run(case.parent, case.name)
    (case / "documents.json").write_text(json.dumps({"case_subjects": {"version": 1, "case_id": case.name,
        "people": [{"id": "b" * 32, "case_role": "applicant", "active": True}]}}))
    before = (case / "documents.json").read_bytes()
    with pytest.raises(purge.PurgeError):
        purge.run(case.parent, case.name)
    assert (case / "documents.json").read_bytes() == before and purge.entry(case.parent, case.name) == central


def action(srv, cid, name, **body):
    return ok(srv, "sam", "/api/engagement", {"client": cid, "action": name} | body)


def test_protected_current_actor_cas_inventory_archive_and_receipt(server, firm):
    cid = client(server, name="Fictional File Policy Client")
    case = firm["clients"] / cid
    case_notes.add_note(case, "Fictional reviewed work product", "Sam Attorney", "attorney")
    route = "/api/engagement?client=" + cid
    before = ok(server, "sam", route)["file_policy"]
    assert before["facts"]["profile_id"] is None
    body = {"client": cid, "action": "file_policy_facts", "expected_revision": 0, "facts": applicable(), "reviewer": "Spoofed Attorney", "role": "attorney"}
    assert call(server, "jane", "/api/engagement", body)[0] == 403
    out = ok(server, "sam", "/api/engagement", body)["file_policy"]
    assert out["history"][-1]["by"] == "Sam Attorney"
    assert call(server, "sam", "/api/engagement", body)[0] == 409
    out = action(server, cid, "file_policy_approve", expected_revision=out["revision"], expected_snapshot_sha256=out["snapshot_sha256"], source_reviewed=True)["file_policy"]
    inv = out["inventory"]
    out = action(server, cid, "inventory_review", expected_revision=out["revision"], expected_snapshot_sha256=out["snapshot_sha256"],
        expected_inventory_sha256=inv["snapshot_sha256"], decisions=choices(inv))["file_policy"]
    out = action(server, cid, "recipient_save", expected_revision=out["revision"], expected_snapshot_sha256=out["snapshot_sha256"], recipient=RECIPIENT)["file_policy"]
    file = action(server, cid, "file", expected_binding_sha256=out["handover"]["binding_sha256"])["file"]
    assert file["binding_current"] and file["integrity_current"] and not file["approval_current"]
    assert call(server, "jane", "/api/case-file.zip?client=" + cid)[0] == 403
    status, raw = call(server, "sam", "/api/case-file.zip?client=" + cid)
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert any(n.endswith("CASE-NOTES.txt") for n in archive.namelist())
    file = action(server, cid, "file_approve", sha256=file["sha256"], expected_binding_sha256=file["binding_sha256"])["file"]
    assert file["approval_current"] and not file["returned"]
    result = action(server, cid, "returned", sha256=file["sha256"], expected_binding_sha256=file["binding_sha256"],
        recipient_sha256=file["binding"]["recipient_sha256"], on="2026-10-05", method="in_person", receipt_reference="Fictional handover acknowledgement")
    assert result["file"]["returned"]["by"] == "Sam Attorney" and result["file"]["returned"]["recipient"]["name"] == RECIPIENT["name"]


def test_protected_gate_wait_rechecks_logged_out_actor_and_does_not_deadlock(server, firm):
    from portal.communication_consent import data_gate
    cid = client(server, name="Fictional Gate Policy Client")
    result = []
    with data_gate(firm["data"]):
        thread = threading.Thread(target=lambda: result.append(call(server, "sam", "/api/engagement", {
            "client": cid, "action": "file_policy_facts", "expected_revision": 0, "facts": applicable()})), daemon=True)
        thread.start()
        # Enter the app lock while the request waits for data_gate. An inverted
        # case/app→installation order would block the cooperating worker here.
        with server["app"]._lock:
            token = server["sam"].split("=", 1)[1]
            server["accounts"].sign_out(token)
    thread.join(timeout=5)
    assert not thread.is_alive() and result and result[0][0] == 401
    assert not (firm["clients"] / cid / policy.FILE).exists()


@pytest.mark.parametrize("name", ["fact_graph.json", "fact_graph_raw.json", "fact_graph_reviewed.json"])
def test_added_removed_canonical_evidence_reopens_approval_without_history_rewrite(case, name):
    out = approve(case)
    history = out["history"]
    (case / name).write_text('{"fictional_current_evidence":true}')
    assert policy.view(case)["state"] == "review_required"
    assert policy.read(case)["history"] == history
    out = policy.view(case)
    policy.approve(case, out["revision"], out["snapshot_sha256"], True, **ACTOR)
    (case / name).unlink()
    assert policy.view(case)["state"] == "review_required"
    assert len(policy.read(case)["history"]) == len(history) + 1


def test_unknown_zip_and_required_cover_cannot_be_included_or_dropped(case):
    path = case / "arbitrary.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("private.json", "Fictional unknown security content")
    out = approve(case)
    inv = client_file.inventory(case, case.parent.parent / "portal")
    ziprow = next(r for r in inv["entries"] if r["path"].endswith("arbitrary.zip"))
    assert not ziprow["reviewable"] and ziprow["category"] == "unsupported"
    for mutate in (lambda row: row.update(include=True) if row["path"] == ziprow["path"] else None,
                   lambda row: row.update(include=False) if row["path"] == "COVER.pdf" else None):
        decided = choices(inv)
        for row in decided:
            mutate(row)
        with pytest.raises(ValueError, match="safe alternative"):
            policy.approve_inventory(case, out["revision"], out["snapshot_sha256"], inv["snapshot_sha256"], decided, **ACTOR)
    assert policy.read(case)["inventory_approval"] is None


def test_actual_q1_wait_second_attorney_and_complete_removal(case, monkeypatch):
    row = scheduled(case)
    assert row["needs_confirm"] and row["policy_authority"] and not row["early"]
    with pytest.raises(purge.PurgeError, match="second attorney"):
        purge.confirm(case.parent, case.name, ACTOR["who"], "attorney")
    with pytest.raises(purge.PurgeError, match="not ready"):
        purge.run(case.parent, case.name)
    purge.confirm(case.parent, case.name, "Second Fictional Attorney", "attorney")
    with pytest.raises(purge.PurgeError, match="not ready"):
        purge.run(case.parent, case.name)
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    purge.run(case.parent, case.name)
    assert not case.exists() and purge.entry(case.parent, case.name)["state"] == "done"


def test_legacy_manual_destroy_unconfirmed_policy_never_records(case):
    end(case)
    with pytest.raises(ValueError, match="destruction approval"):
        engagement.mark_destroyed(case.parent, case.name, **ACTOR, folder_removed=False, export_kept=True, note="Typed reason")
    assert not engagement.read(case).get("destroyed") and not engagement.destroyed(case.parent)


def test_protected_competing_frontdesk_and_policy_enter_gate_before_app_or_case_locks(server, firm, monkeypatch):
    from contextlib import contextmanager
    from portal import communication_consent
    cid = client(server, name="Fictional Concurrent Policy Client")
    name = "Other Fictional Concurrent Client"
    search = ok(server, "jane", "/api/conflict-search", {"purpose": "add", "name": name})
    original_gate = communication_consent.data_gate
    entered = [threading.Event(), threading.Event()]
    results = []
    observed_lock = threading.Lock()
    observed_count = [0]
    @contextmanager
    def observed(data):
        if threading.current_thread() is not threading.main_thread():
            with observed_lock:
                index = observed_count[0]
                observed_count[0] += 1
                if index < len(entered):
                    entered[index].set()
        with original_gate(data):
            yield
    monkeypatch.setattr(communication_consent, "data_gate", observed)
    def send(who, payload):
        results.append(call(server, who, "/api/client-add" if who == "jane" else "/api/engagement", payload))
    with original_gate(firm["data"]):
        front = threading.Thread(target=send, name="frontdesk-policy-probe", args=("jane", {"name": name, "phone": "", "email": "other-fictional@example.test",
            "language": "en", "filing": "i485", "invite": False, "conflict": {"search": search["id"], "decision": "none"}}), daemon=True)
        legal = threading.Thread(target=send, name="policy-policy-probe", args=("sam", {"client": cid, "action": "file_policy_facts", "expected_revision": 0, "facts": applicable()}), daemon=True)
        front.start()
        legal.start()
        assert entered[0].wait(3) and entered[1].wait(3)
        # Both actual HTTP server threads are now waiting for the installation gate.
        acquired = server["app"]._lock.acquire(timeout=1)
        assert acquired, "A waiting request held the app lock before the installation gate"
        server["app"]._lock.release()
    front.join(timeout=5)
    legal.join(timeout=5)
    assert not front.is_alive() and not legal.is_alive()
    assert len(results) == 2 and all(status == 200 for status, _ in results), results


def test_same_bytes_different_own_source_pointer_invalidates_inventory_review(case):
    first, second = case / "source-first", case / "source-second"
    for folder in (first, second):
        folder.mkdir()
        (folder / "original.txt").write_text("Identical fictional original bytes")
    (case / "meta.json").write_text(json.dumps({"source_folder": str(first)}))
    ready(case)
    before = policy.read(case)
    (case / "meta.json").write_text(json.dumps({"source_folder": str(second)}))
    with pytest.raises(ValueError, match="changed"):
        policy.handover_binding(case)
    assert policy.read(case)["history"] == before["history"]
    assert not policy.display(case)["inventory"]["approval"]["current"]


@pytest.mark.parametrize("area", ["clients", "documents", "portal"])
@pytest.mark.parametrize("folder", ["auth", "consent-evidence", "staff-upload-receipts", "processing-receipts", "source-authorization", "family-operations"])
def test_private_directory_is_hard_internal_before_source_registration(area, folder):
    prefix = "uploads/" if area == "portal" else ""
    assert client_file.internal(area, prefix + folder + "/fictional-private.pdf")
    assert client_file.internal(area, prefix + folder.upper() + "/fictional-private.txt")


def test_known_external_case_document_root_never_authorizes_another_case(case):
    other = case.parent.parent.parent / "clients" / "other-fictional-case" / "source"
    other.mkdir(parents=True)
    (other / "private.txt").write_text("Fictional other case secret")
    (case / "meta.json").write_text(json.dumps({"source_folder": str(other)}))
    with pytest.raises(ValueError, match="protected"):
        client_file.inventory(case, case.parent.parent / "portal")


@pytest.mark.parametrize("root", [".", "staff-upload-receipts", "uploads-auth"])
def test_source_pointer_does_not_release_own_private_bytes_as_originals(case, root):
    if root == "uploads-auth":
        source = case.parent.parent / "portal" / "clients" / case.name / "uploads" / "auth"
    elif root == ".":
        source = case
    else:
        source = case / root
    source.mkdir(parents=True, exist_ok=True)
    private = (source / "staff-upload-receipts" / "fictional-private.txt") if root == "." else source / "fictional-private.txt"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("Fictional private operation secret")
    (case / "meta.json").write_text(json.dumps({"source_folder": str(source)}))
    selected, _, _ = client_file.gather(case, case.parent.parent / "portal")
    assert not any(row.source == private for row in selected)


@pytest.mark.parametrize("folder", ["auth", "staff-upload-receipts"])
def test_private_children_of_valid_external_own_source_never_release_bytes(case, folder):
    source = case.parent.parent.parent / "clients" / case.name / "source"
    private = source / folder / "private.pdf"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"%PDF-1.4 fictional private authority bytes")
    (source / "original.txt").write_text("Fictional legitimate original")
    (case / "meta.json").write_text(json.dumps({"source_folder": str(source)}))
    selected, _, _ = client_file.gather(case, case.parent.parent / "portal")
    assert private not in [row.source for row in selected]
    assert any(row.source and row.source.name == "original.txt" for row in selected)
    assert not any(row["path"].endswith("private.pdf") for row in client_file.inventory(case, case.parent.parent / "portal")["entries"])


def test_protected_download_never_sends_replaced_bytes_after_path_validation(server, firm, monkeypatch):
    cid = client(server, name="Fictional Archive Capture Client")
    case = firm["clients"] / cid
    file = prepare(case)["file"]
    archive = engagement.file_path(case, case.parent, firm["portal"])
    before = (case / engagement.FILE).read_bytes()
    original = client_file.capture
    replacement = b"Fictional replacement bytes after path validation"
    def swap(path, root, *args, **kwargs):
        if path == archive:
            path.write_bytes(replacement)
        return original(path, root, *args, **kwargs)
    monkeypatch.setattr(client_file, "capture", swap)
    status, payload = call(server, "sam", "/api/case-file.zip?client=" + cid)
    assert status == 404 and replacement not in payload
    assert (case / engagement.FILE).read_bytes() == before
    assert engagement.read(case)["file"]["approved"]["sha256"] == file["sha256"]


def test_protected_download_compares_final_capture_after_first_archive_inspection(server, firm, monkeypatch):
    cid = client(server, name="Fictional Final Capture Client")
    case = firm["clients"] / cid
    file = prepare(case)["file"]
    archive = engagement.file_path(case, case.parent, firm["portal"])
    before = (case / engagement.FILE).read_bytes()
    original = client_file.capture
    seen = []
    replacement = b"Fictional new bytes only after successful initial inspection"
    def swap(path, root, *args, **kwargs):
        if path == archive:
            seen.append(path)
            if len(seen) == 2:
                path.write_bytes(replacement)
        return original(path, root, *args, **kwargs)
    monkeypatch.setattr(client_file, "capture", swap)
    status, payload = call(server, "sam", "/api/case-file.zip?client=" + cid)
    assert len(seen) == 2 and status == 404 and replacement not in payload
    assert (case / engagement.FILE).read_bytes() == before
    assert engagement.read(case)["file"]["approved"]["sha256"] == file["sha256"]
