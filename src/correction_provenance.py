"""Read-time source bindings for workflow observations, never label adjudication.

Missing historical bindings remain missing. This code never constructs a current
reader manifest to fill a historical record, and never invokes a reader or OCR.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from document_instances import digest


def observation(how):
    return {"kind": {"confirm": "workflow_confirmation", "correction": "workflow_correction",
                     "hand_filled_form": "workflow_reference_comparison"}[how],
            "adjudication": "pending", "training_authorized": False,
            "evaluation_reference": False, "staff_seconds": None}


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def retain(case_dir, src, at, confirmation=None, snapshots=None, *, field_key=None):
    """Verify the source's stored read-time identity against retained original bytes."""
    from review import evidence
    result = {"state": "legacy_unverified", "source_version": src.get("evidence_version"),
              "instance_id": src.get("instance_id"), "source_sha256": None,
              "evidence_fingerprint": None, "boundary_fingerprint": None,
              "reader_manifest": None, "reader_manifest_sha256": None,
              "original_range": None, "reason": "The historical read-time source identity was not recorded."}
    if not isinstance(result["source_version"], str) or not result["source_version"] or not isinstance(result["instance_id"], str) or not result["instance_id"]:
        return result
    read_at, decided_at = _time(src.get("extracted_at")), _time(at)
    if read_at is None or decided_at is None:
        result["reason"] = "The read/decision time binding is unavailable."
        return result
    if read_at > decided_at:
        result.update(state="stale", reason="This read is newer than the decision; no historical provenance was backfilled.")
        return result
    if not isinstance(field_key, str) or not field_key or not isinstance(src.get("subject_role"), str) or not src["subject_role"]:
        result["reason"] = "The historical field key or printed source role binding is unavailable."
        return result
    expected = None
    decision_proof = None
    if confirmation is not None:
        proof = confirmation.get("proof") if isinstance(confirmation, dict) else None
        entries = proof.get("evidence") if isinstance(proof, dict) else None
        if not isinstance(entries, list):
            result.update(state="stale", reason="The decision-time source proof is missing or malformed.")
            return result
        matches = [p for p in entries
                   if isinstance(p, dict) and isinstance(p.get("source"), dict)
                   and p["source"].get("doc_id") == src.get("doc_id")
                   and p["source"].get("doc_type") == src.get("doc_type")
                   and p["source"].get("instance_id") == src.get("instance_id")
                   and p["source"].get("subject_role") == src.get("subject_role")
                   and p["source"].get("evidence_version") == src.get("evidence_version")
                   and p["source"].get("raw_value") == src.get("raw_value")
                   and p["source"].get("normalized_value") == src.get("normalized_value")
                   and p["source"].get("page") == src.get("page")
                   and p["source"].get("read_manifest") == src.get("read_manifest")]
        if len(matches) != 1:
            result.update(state="stale", reason="The observation no longer matches its decision-time source proof.")
            return result
        decision_proof = matches[0]
        expected = decision_proof.get("source_sha256")
        if not expected:
            return result
    key = (src.get("doc_id"), expected)
    snapshots = snapshots if snapshots is not None else {}
    try:
        if key not in snapshots:
            snapshots[key] = evidence.snapshot(Path(case_dir), src.get("doc_id"), expected)
        source = snapshots[key]
        location = source.location(src.get("page"), src.get("instance_id"))
        if location["state"] != "current" or location.get("instance_id") != src.get("instance_id"):
            result.update(state="stale", reason="The retained original does not match the read-time instance.")
            return result
        part = next((p for p in (source.plan or {}).get("instances", []) if p.get("instance_id") == src.get("instance_id")), None)
        if part is None or len(source.data) > 64 * 1024 * 1024:
            result.update(state="unavailable", reason="The bounded original source proof is unavailable.")
            return result
        from extract.base import ExtractedField
        from subject_attribution import provenance
        field = ExtractedField(field_key, src.get("raw_value"), src.get("normalized_value"), src.get("confidence"), src.get("page"))
        current_binding = provenance(part, src.get("doc_type"), field)
        if any(current_binding[name] != src.get(name) for name in ("instance_id", "evidence_version", "subject_role")):
            result.update(state="stale", reason="The read-time field/role/reader/boundary version no longer matches the retained source.")
            return result
        if decision_proof is not None and (
            decision_proof.get("original_zero_based_inclusive_range") != location.get("original_range")
            or not decision_proof.get("boundary_fingerprint")
            or decision_proof["boundary_fingerprint"] != part.get("evidence_fingerprint")
        ):
            result.update(state="stale", reason="The current source boundary/range does not match its decision-time proof.")
            return result
        manifest = src.get("read_manifest")
        if manifest is not None and not isinstance(manifest, dict):
            result.update(state="unavailable", reason="The stored reader manifest is malformed.")
            return result
        result.update(state="verified", source_sha256=source.sha256,
                      evidence_fingerprint=digest({k: v for k, v in src.items() if k != "extracted_at"}),
                      boundary_fingerprint=part.get("evidence_fingerprint"),
                      reader_manifest=manifest, reader_manifest_sha256=digest(manifest) if manifest is not None else None,
                      original_range=location.get("original_range"),
                      reason="Read-time identity matches the retained original; workflow value still awaits independent adjudication."
                      if manifest is not None else "Source identity verified; the original reader configuration was not recorded.")
    except (evidence.Unavailable, OSError, ValueError, TypeError, KeyError):
        result.update(state="stale", reason="The original changed, is missing, or has no verifiable decision-time binding.")
    return result
