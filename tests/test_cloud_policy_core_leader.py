"""Independent adverse policy probes using retained fictional case originals."""
import json
import uuid
from datetime import datetime

import pytest

import clock
import client_file_policy as policy
from test_name_cards import _retained_name_case
from test_name_events import MA_AFTER

ACTOR = {"who": "Independent Fictional Attorney", "role": "attorney"}


def selected_case(tmp_path, monkeypatch, **extra):
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 12, 0))
    case = _retained_name_case(tmp_path, MA_AFTER)
    portal = tmp_path / "isolated-policy-portal"
    current = policy.view(case, portal)
    assert current["state"] != "approved"
    assert current["facts"]["profile_id"] is None
    value = current["facts"] | {"profile_id": policy.profile()["id"], "choice_of_law_basis": "principal_office",
        "attorney_admissions": ["MA"], "principal_office": "Fictional principal office",
        "principal_office_jurisdiction": "MA", "applicability_reason": "Fictional attorney choice-of-law determination for this test."} | extra
    current = policy.save_facts(case, current["revision"], value, portal_root=portal, **ACTOR)
    current = policy.approve(case, current["revision"], current["snapshot_sha256"], True, portal_root=portal, **ACTOR)
    assert current["state"] == "approved"
    return case, portal, current


def test_current_policy_allows_protective_handover_while_destruction_is_held(tmp_path, monkeypatch):
    case, portal, current = selected_case(tmp_path, monkeypatch,
        holds=[{"kind": "preservation", "description": "Fictional pending preservation requirement", "active": True}])
    assert policy.require_handover(case, portal) == current["snapshot_sha256"]
    assert current["destruction"]["state"] != "approved"
    with pytest.raises(ValueError):
        policy.require_destruction(case, portal)
    # Stale UI state cannot overwrite the hold or create a fresh approval.
    with pytest.raises(ValueError):
        policy.save_facts(case, current["revision"] - 1, current["facts"] | {"holds": []}, portal_root=portal, **ACTOR)
    assert policy.view(case, portal)["facts"]["holds"][0]["active"] is True


def test_changed_profile_content_invalidates_approval_without_deleting_history(tmp_path, monkeypatch):
    import schema_path
    case, portal, current = selected_case(tmp_path, monkeypatch)
    original = schema_path.path
    path = original("law", "client_file_policy_ma")
    changed = json.loads(path.read_text())
    changed["handover"] += " Fictional changed source text."
    replacement = tmp_path / "changed-profile.json"
    replacement.write_text(json.dumps(changed))
    monkeypatch.setattr(schema_path, "path", lambda kind, name, *a, **kw: replacement if (kind, name) == ("law", "client_file_policy_ma") else original(kind, name, *a, **kw))
    after = policy.view(case, portal)
    assert after["state"] != "approved"
    assert after["history"] == current["history"]
    assert after["applicability_approval"]["by"] == ACTOR["who"]
    with pytest.raises(ValueError):
        policy.require_handover(case, portal)


@pytest.mark.parametrize("corrupt_date", ["2010-01-01", 123, "0001-01-01"])
def test_corrupt_or_below_minimum_saved_retention_cannot_be_current_approval(tmp_path, monkeypatch, corrupt_date):
    case, portal, current = selected_case(tmp_path, monkeypatch, matter_type="civil", client_age_status="adult",
        representation_completed_on="2010-10-05", originals_status="none_held", other_requirements="reviewed_none")
    current = policy.approve_retention(case, current["revision"], current["snapshot_sha256"], "2016-10-05",
        "Fictional reviewed ordinary retention decision.", portal_root=portal, **ACTOR)
    assert current["retention"]["state"] == "approved"
    path = case / policy.FILE
    record = json.loads(path.read_text())
    record["retention_approval"]["keep_until"] = corrupt_date
    path.write_text(json.dumps(record))
    try:
        current = policy.view(case, portal)
    except ValueError:
        return
    assert current["retention"]["state"] != "approved"
    assert current["destruction"]["state"] != "approved"


def test_changed_case_incarnation_cannot_reuse_saved_policy(tmp_path, monkeypatch):
    case, portal, current = selected_case(tmp_path, monkeypatch)
    path = case / "documents.json"
    catalog = json.loads(path.read_text())
    applicant = next(row for row in catalog["case_subjects"]["people"] if row.get("case_role") == "applicant")
    applicant["id"] = uuid.uuid4().hex
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError):
        policy.require_handover(case, portal)
    record = json.loads((case / policy.FILE).read_text())
    assert record["history"] == current["history"]


def test_keep_until_today_has_not_yet_passed_for_destruction(tmp_path, monkeypatch):
    case, portal, current = selected_case(tmp_path, monkeypatch, matter_type="civil", client_age_status="adult",
        representation_completed_on="2010-10-05", originals_status="none_held", other_requirements="reviewed_none")
    current = policy.approve_retention(case, current["revision"], current["snapshot_sha256"], "2026-10-05",
        "Fictional attorney directs keeping the record through this date.", portal_root=portal, **ACTOR)
    with pytest.raises(ValueError):
        policy.approve_destruction(case, current["revision"], current["snapshot_sha256"], True,
            "Fictional destruction determination.", portal_root=portal, **ACTOR)
    assert policy.view(case, portal)["destruction"]["state"] != "approved"
