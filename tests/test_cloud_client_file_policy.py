"""Fictional policy-core checks; no legal application, delivery or deletion."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

import client_file_policy as policy
import clock
import settings

ACTOR = {"who": "Fictional Policy Attorney", "role": "attorney"}


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "installation"
    d = root / "data" / "clients" / "fictional-policy-case"
    d.mkdir(parents=True)
    (d / "documents.json").write_text(json.dumps({"case_subjects": {"version": 1, "case_id": d.name,
        "people": [{"id": "a" * 32, "case_role": "applicant", "active": True}]}}))
    monkeypatch.setattr(settings, "PATH", root / "data" / "settings.json")
    for key, value in {"I485_SETTINGS": "settings.json", "I485_MAINTENANCE_LOG": "maintenance_log.json",
                       "I485_RULES_APPROVED": "rules_approved.json", "I485_EVENTS": "events.jsonl",
                       "I485_CASES": "clients", "I485_PROSPECTS": "prospects", "PORTAL_DATA": "portal", "I485_JOBS": "jobs"}.items():
        monkeypatch.setenv(key, str(root / "data" / value))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 12, tzinfo=timezone.utc))
    return d


def applicable():
    return copy.deepcopy(policy.DEFAULT_FACTS) | {"profile_id": policy.profile()["id"],
        "choice_of_law_basis": "principal_office", "attorney_admissions": ["MA"],
        "principal_office": "Fictional office", "principal_office_jurisdiction": "MA",
        "applicability_reason": "Fictional attorney independently reviewed Rule 8.5 and the matter."}


def approve(case, value=None):
    current = policy.view(case)
    current = policy.save_facts(case, current["revision"], value or applicable(), **ACTOR)
    return policy.approve(case, current["revision"], current["snapshot_sha256"], True, **ACTOR)


def destructive():
    return applicable() | {"matter_type": "civil", "client_age_status": "adult",
        "representation_completed_on": "2010-01-01", "originals_status": "returned",
        "other_requirements": "reviewed_none"}


def retention(case, value=None, keep_until="2016-01-01"):
    current = approve(case, value or destructive())
    return policy.approve_retention(case, current["revision"], current["snapshot_sha256"], keep_until,
                                   "Fictional attorney determination after current legal review.", **ACTOR)


def destruction(case):
    current = retention(case)
    return policy.approve_destruction(case, current["revision"], current["snapshot_sha256"], True,
                                     "Fictional attorney independently approves this destruction.", **ACTOR)


def test_no_selection_or_residence_infers_jurisdiction(case):
    settings.save("firm", {"firm.state": "MA"}, "Fictional Setup")
    out = policy.view(case)
    assert out["facts"]["profile_id"] is None and out["state"] == "review_required"
    assert not (case / policy.FILE).exists()
    with pytest.raises(ValueError, match="jurisdiction policy"):
        policy.require_handover(case)
    with pytest.raises(ValueError, match="destruction approval"):
        policy.require_destruction(case)


def test_unknown_retention_does_not_block_protective_handover(case):
    out = approve(case)
    assert out["state"] == "approved" and out["handover"]["policy_ready"]
    assert out["retention"]["state"] == out["destruction"]["state"] == "held"
    assert policy.require_handover(case) == out["snapshot_sha256"]
    assert out["facts"]["representation_completed_on"] is None


@pytest.mark.parametrize("key,value", [("profile_id", "FL-DEFAULT"), ("client_age_status", "typed_reason"),
                                     ("holds", [{"kind": "other", "description": "hold", "active": 1}]),
                                     ("representation_completed_on", "2099-01-01"), ("attorney_admissions", "MA")])
def test_strict_typed_facts_fail_without_publication(case, key, value):
    wrong = applicable() | {key: value}
    with pytest.raises(ValueError):
        policy.save_facts(case, 0, wrong, **ACTOR)
    assert not (case / policy.FILE).exists()


@pytest.mark.parametrize("role", [None, "paralegal", "staff", ""])
def test_only_explicit_attorney_can_make_policy_determination(case, role):
    with pytest.raises(PermissionError):
        policy.save_facts(case, 0, applicable(), who="Fictional Actor", role=role)
    assert not (case / "client-file.lock").exists()


def test_stale_revision_and_snapshot_preserve_exact_bytes(case):
    out = approve(case)
    before = (case / policy.FILE).read_bytes()
    with pytest.raises(ValueError, match="changed"):
        policy.save_facts(case, out["revision"] - 1, applicable(), **ACTOR)
    with pytest.raises(ValueError, match="changed"):
        policy.approve(case, out["revision"], "f" * 64, True, **ACTOR)
    assert (case / policy.FILE).read_bytes() == before


def test_source_change_reopens_approval_with_history(case, tmp_path, monkeypatch):
    old = approve(case)
    original = policy.profile()
    path = tmp_path / "changed-profile.json"
    path.write_text(json.dumps(original | {"handover": "A changed legal policy for fresh attorney review."}))
    original_path = policy.schema_path.path
    monkeypatch.setattr(policy.schema_path, "path", lambda kind, name, *a, **kw:
                        path if kind == "law" and name == "client_file_policy_ma" else original_path(kind, name, *a, **kw))
    out = policy.view(case)
    assert out["state"] == "review_required" and not out["applicability_approval"]["current"]
    assert out["history"] == old["history"] and out["applicability_approval"]["by"] == ACTOR["who"]


def test_operational_end_invalidates_but_is_not_legal_completion(case):
    approve(case)
    (case / "engagement.json").write_text(json.dumps({"end": {"on": "2010-01-01", "state": "closed"}}))
    assert policy.view(case)["state"] == "review_required"
    assert policy.view(case)["facts"]["representation_completed_on"] is None


def test_recreated_same_id_and_copied_record_never_reuses_approval(case):
    approve(case)
    (case / "documents.json").write_text(json.dumps({"case_subjects": {"version": 1, "case_id": case.name,
        "people": [{"id": "b" * 32, "case_role": "applicant", "active": True}]}}))
    with pytest.raises(ValueError, match="identity changed"):
        policy.require_handover(case)


@pytest.mark.parametrize("matter", ["criminal", "delinquency", "cpcs", "other", "unknown"])
def test_special_matter_held_while_protective_handover_allowed(case, matter):
    out = approve(case, destructive() | {"matter_type": matter})
    assert out["handover"]["policy_ready"]
    with pytest.raises(ValueError, match="unsupported"):
        policy.approve_retention(case, out["revision"], out["snapshot_sha256"], "2016-01-01", "Reason", **ACTOR)


@pytest.mark.parametrize("changes", [
    {"holds": [{"kind": "preservation", "description": "Fictional active hold", "active": True}]},
    {"client_age_status": "unknown"}, {"originals_status": "retained_for_client"},
    {"other_requirements": "unknown"}, {"representation_completed_on": None}])
def test_text_never_overrides_destruction_protections(case, changes):
    out = approve(case, destructive() | changes)
    with pytest.raises(ValueError):
        policy.approve_retention(case, out["revision"], out["snapshot_sha256"], "2016-01-01", "Destroy anyway", **ACTOR)
    with pytest.raises(ValueError, match="reason cannot override"):
        policy.approve_destruction(case, out["revision"], out["snapshot_sha256"], True, "Destroy anyway", **ACTOR)


def test_elapsed_retention_alone_is_not_destruction_approval(case):
    out = retention(case)
    assert out["retention"]["state"] == "approved" and out["destruction"]["state"] == "held"
    with pytest.raises(ValueError, match="destruction approval"):
        policy.require_destruction(case)
    out = policy.approve_destruction(case, out["revision"], out["snapshot_sha256"], True, "Reviewed", **ACTOR)
    assert out["destruction"]["state"] == "approved"
    assert policy.require_destruction(case)["snapshot_sha256"] == out["snapshot_sha256"]


def test_minimum_and_minor_majority_floor_never_auto_grant(case):
    out = approve(case, destructive() | {"client_age_status": "minor", "date_of_majority": "2020-12-31"})
    with pytest.raises(ValueError, match="minimum"):
        policy.approve_retention(case, out["revision"], out["snapshot_sha256"], "2026-12-30", "Reason", **ACTOR)
    out = policy.approve_retention(case, out["revision"], out["snapshot_sha256"], "2026-12-31", "Reviewed", **ACTOR)
    assert out["retention"]["state"] == "approved"
    with pytest.raises(ValueError, match="elapsed"):
        policy.approve_destruction(case, out["revision"], out["snapshot_sha256"], True, "Reason", **ACTOR)


def test_new_hold_invalidates_retention_and_destruction_without_overwriting_history(case):
    out = destruction(case)
    old = copy.deepcopy(out["destruction"]["approval"])
    changed = out["facts"] | {"holds": [{"kind": "court_order", "description": "Fictional order", "active": True}]}
    out = policy.save_facts(case, out["revision"], changed, **ACTOR)
    assert out["destruction"]["approval"]["by"] == old["by"]
    assert out["destruction"]["state"] == "held" and not out["destruction"]["approval"]["current"]
    with pytest.raises(ValueError):
        policy.require_destruction(case)


def test_recipient_requires_current_policy_and_explicit_authority(case):
    out = approve(case)
    value = {"name": "Fictional Client", "authority": "client", "authority_evidence": "Current client record reviewed",
             "method": "in_person", "destination": "", "authority_reviewed": True}
    out = policy.save_recipient(case, out["revision"], out["snapshot_sha256"], value, **ACTOR)
    assert out["recipient"]["current"] and out["recipient"]["role"] == "attorney"
    assert not any(name in out for name in ("delivered", "sent", "receipt"))
    before = (case / policy.FILE).read_bytes()
    with pytest.raises(ValueError):
        policy.save_recipient(case, out["revision"], out["snapshot_sha256"], value | {"authority_reviewed": False}, **ACTOR)
    assert (case / policy.FILE).read_bytes() == before


def test_interrupted_publication_denies_prior_approval_and_preserves_residue(case, monkeypatch):
    out = destruction(case)
    before = (case / policy.FILE).read_bytes()
    def fail(*args):
        raise OSError("Synthetic atomic publication failure")
    monkeypatch.setattr(policy.os, "replace", fail)
    with pytest.raises(OSError, match="Synthetic"):
        policy.save_facts(case, out["revision"], out["facts"] | {"other_requirements": "unknown"}, **ACTOR)
    assert (case / policy.FILE).read_bytes() == before and (case / policy.TEMP).exists()
    with pytest.raises(ValueError, match="interrupted"):
        policy.require_destruction(case)
    with pytest.raises(ValueError, match="interrupted"):
        policy.save_facts(case, out["revision"], out["facts"], **ACTOR)


def test_no_case_identity_is_created_implicitly(case):
    (case / "documents.json").unlink()
    with pytest.raises(ValueError, match="identity"):
        policy.save_facts(case, 0, applicable(), **ACTOR)
    assert not (case / "documents.json").exists() and not (case / policy.FILE).exists()


@pytest.mark.parametrize("kind,key,value", [
    ("retention_approval", "keep_until", "2016-99-99"),
    ("retention_approval", "keep_until", None),
    ("retention_approval", "role", None),
    ("destruction_approval", "protections_reviewed", 1),
    ("applicability_approval", "source_reviewed", False),
    ("applicability_approval", "at", "2026-10-05T12:00:00"),
])
def test_malformed_persisted_approvals_fail_closed(case, kind, key, value):
    destruction(case)
    record = policy.read(case)
    record[kind][key] = value
    (case / policy.FILE).write_text(json.dumps(record))
    with pytest.raises(ValueError):
        policy.require_destruction(case)


def test_shortened_persisted_retention_is_rechecked_not_lexically_accepted(case):
    destruction(case)
    record = policy.read(case)
    record["retention_approval"]["keep_until"] = "2010-01-01"
    record["destruction_approval"]["retention_sha256"] = policy.digest(record["retention_approval"])
    (case / policy.FILE).write_text(json.dumps(record))
    assert policy.view(case)["retention"]["state"] == "held"
    with pytest.raises(ValueError):
        policy.require_destruction(case)


def test_age_today_does_not_override_recorded_minor_at_completion(case):
    value = destructive() | {"client_age_status": "adult", "date_of_majority": "2020-01-01"}
    out = approve(case, value)
    assert any("conflicts" in text for text in out["retention"]["holds"])
    with pytest.raises(ValueError):
        policy.approve_retention(case, out["revision"], out["snapshot_sha256"], "2016-01-01", "Adult now", **ACTOR)


def test_future_majority_and_leap_anniversary_are_explicit(case):
    out = approve(case, destructive() | {"client_age_status": "minor", "date_of_majority": "2028-02-29"})
    with pytest.raises(ValueError, match="minimum"):
        policy.approve_retention(case, out["revision"], out["snapshot_sha256"], "2034-02-27", "Reviewed", **ACTOR)
    out = policy.approve_retention(case, out["revision"], out["snapshot_sha256"], "2034-02-28", "Reviewed", **ACTOR)
    assert out["retention"]["approval"]["keep_until"] == "2034-02-28"
    assert out["destruction"]["state"] == "held"


def test_cannot_create_revision_zero_approval_record(case):
    out = policy.view(case)
    with pytest.raises(ValueError, match="Save the current"):
        policy.approve(case, 0, out["snapshot_sha256"], True, **ACTOR)
    assert not (case / policy.FILE).exists()


def test_changed_office_invalidates_current_authority(case):
    approve(case)
    settings.save("firm", {"firm.state": "FL"}, "Fictional Setup")
    assert policy.view(case)["state"] == "review_required"


def test_original_portal_enrollment_wins_and_replacement_cannot_fall_back(case):
    from portal.store import PortalStore
    root = case.parent.parent / "portal"
    store = PortalStore(root)
    store.add_client(case.name, "Fictional Policy Client", email="fictional@example.test", language="en")
    out = approve(case)
    before = policy.read(case)["identity"]
    access_path = root / "clients" / case.name / "portal_access.json"
    access = json.loads(access_path.read_text())
    start = next(row for row in access["history"] if row["action"] == "enrollment_denied")
    start["id"] = "c" * 32
    access_path.write_text(json.dumps(access))
    assert policy.identity(case) != before
    with pytest.raises(ValueError, match="identity changed"):
        policy.require_handover(case)
    assert (case / policy.FILE).read_text().find(out["applicability_approval"]["by"]) >= 0
    access_path.unlink()
    with pytest.raises(ValueError, match="enrollment identity"):
        policy.identity(case)


@pytest.mark.parametrize("key,value", [("facts_revision", 99), ("profile", {"id": "MA-CLIENT-FILE-DRAFT", "version": "wrong", "source_digest": "d" * 64})])
def test_approval_metadata_must_match_semantic_current_record(case, key, value):
    destruction(case)
    record = policy.read(case)
    record["applicability_approval"][key] = value
    (case / policy.FILE).write_text(json.dumps(record))
    assert policy.view(case)["state"] == "review_required"
    with pytest.raises(ValueError):
        policy.require_destruction(case)


def test_internal_policy_cannot_be_released_by_document_registration(case):
    import client_file
    approve(case)
    catalog = json.loads((case / "documents.json").read_text())
    catalog["documents"] = [{"files": [policy.FILE], "type": "other", "owner": "applicant"}]
    (case / "documents.json").write_text(json.dumps(catalog))
    entries, excluded, warnings = client_file.gather(case, case.parent.parent / "portal")
    assert not any(entry.source == case / policy.FILE for entry in entries)
    assert any(row["path"].endswith("/" + policy.FILE) and "Internal jurisdiction" in row["why"] for row in excluded)


def test_keeping_date_is_inclusive_and_destruction_requires_following_day(case, monkeypatch):
    out = retention(case, keep_until="2026-10-05")
    with pytest.raises(ValueError, match="elapsed"):
        policy.approve_destruction(case, out["revision"], out["snapshot_sha256"], True, "Reviewed", **ACTOR)
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 6, 12, tzinfo=timezone.utc))
    out = policy.approve_destruction(case, out["revision"], out["snapshot_sha256"], True, "Reviewed", **ACTOR)
    assert out["destruction"]["state"] == "approved"
    # Recheck the unsafe on-date approval that the prior core could persist.
    # Keep its actor/binding/time current so denial tests the inclusive date,
    # rather than only rejecting a future approval timestamp.
    record = policy.read(case)
    record["destruction_approval"]["at"] = record["retention_approval"]["at"]
    (case / policy.FILE).write_text(json.dumps(record))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 12, tzinfo=timezone.utc))
    out = policy.view(case)
    assert out["destruction"]["approval"]["current"] and out["destruction"]["state"] == "held"
    with pytest.raises(ValueError, match="destruction approval"):
        policy.require_destruction(case)
