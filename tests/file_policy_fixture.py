"""Explicit fictional attorney policy determinations for positive lifecycle tests.

Call these only inside a positive scenario, never an installation/firm or generic
ended-case fixture. No approval flags are injected and no source review is forged.
Dates/age are supplied by that scenario; operational closure never fills legal facts.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import client_file_policy as policy
import subject_attribution


RECIPIENT = {"name": "Fictional Client Recipient", "authority": "client", "authority_evidence": "Fictional actual current client authority reviewed",
             "method": "in_person", "destination": "", "authority_reviewed": True}


def own_case_identity(case):
    """Establish the existing fictional own applicant catalog, not a source mapping."""
    path = Path(case) / "documents.json"
    data = json.loads(path.read_text()) if path.exists() else {"client_id": Path(case).name, "documents": []}
    subject_attribution.ensure_catalog(data, Path(case).name)
    path.write_text(json.dumps(data))


def applicability(case, *, who, portal_root=None, value=None):
    actor = {"who": who, "role": "attorney", "portal_root": portal_root}
    facts = copy.deepcopy(policy.DEFAULT_FACTS) | {"profile_id": policy.profile()["id"], "choice_of_law_basis": "principal_office",
        "attorney_admissions": ["MA"], "principal_office": "Fictional Massachusetts office", "principal_office_jurisdiction": "MA",
        "applicability_reason": "Fictional attorney independently reviewed official sources and Rule 8.5 applicability for this specific matter."}
    if value is not None:
        facts.update(value)
    out = policy.view(case, portal_root)
    out = policy.save_facts(case, out["revision"], facts, **actor)
    return policy.approve(case, out["revision"], out["snapshot_sha256"], True, **actor)


def handover(case, *, who, portal_root=None, include_work_product=False):
    actor = {"who": who, "role": "attorney", "portal_root": portal_root}
    out = applicability(case, **{k: v for k, v in actor.items() if k != "role"})
    inv = policy.display(case, portal_root)["inventory"]
    assert not inv["warnings"] and inv["state"] != "unavailable", inv
    decisions = [{"path": row["path"], "sha256": row["sha256"], "input_sha256": row["input_sha256"],
        "include": row["reviewable"] and (row["category"] == "material" or include_work_product),
        "reason": "Fictional actual attorney review: include requested material, exclude other drafts/unsupported records and arrange a safe alternative if requested."}
        for row in inv["entries"]]
    out = policy.approve_inventory(case, out["revision"], out["snapshot_sha256"], inv["snapshot_sha256"], decisions, **actor)
    policy.save_recipient(case, out["revision"], out["snapshot_sha256"], RECIPIENT, **actor)
    return policy.display(case, portal_root)["handover"]["binding_sha256"]


def disposition(case, *, who, completed_on, age_status, keep_until, majority_on=None, portal_root=None, originals_status="none_held"):
    actor = {"who": who, "role": "attorney", "portal_root": portal_root}
    out = applicability(case, who=who, portal_root=portal_root, value={"matter_type": "civil",
        "client_age_status": age_status, "representation_completed_on": completed_on, "date_of_majority": majority_on,
        "originals_status": originals_status, "other_requirements": "reviewed_none"})
    out = policy.approve_retention(case, out["revision"], out["snapshot_sha256"], keep_until,
        "Fictional attorney independently reviewed current keeping period and supported minimum, not inferred from operational closure.", **actor)
    return policy.approve_destruction(case, out["revision"], out["snapshot_sha256"], True,
        "Fictional attorney reviewed resolved protections and explicitly approves current destruction after the inclusive keeping date.", **actor)


def archive_arguments(case):
    import engagement
    row = engagement.read(case)["file"]
    return {"expected_binding_sha256": row["binding_sha256"]}


def receipt_arguments(case):
    import engagement
    row = engagement.read(case)["file"]
    return archive_arguments(case) | {"recipient_sha256": row["binding"]["recipient_sha256"],
        "receipt_reference": "Fictional actual client acknowledgement of this exact approved archive"}
