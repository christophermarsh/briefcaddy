"""Explicit local, own-firm candidate references; no adjudication or training.

An authorization and its retained structured approval bind the exact selected
example IDs/source metadata. This is local file-backed approval evidence, not a
signed attestation. The exporter never scans all clients or copies source PDFs.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import replace
from pathlib import Path

from evaluation.corpus import canonical, digest, load

import clock
import restricted
from review.auth import Accounts

CASE = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}")
EXAMPLE = re.compile(r"[0-9a-f]{20}")


def _safe(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Symlink paths are not authorized for candidate export.")
        if part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise ValueError("Reparse paths are not authorized for candidate export.")
    return path


def _read(path):
    path = _safe(path)
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("The bounded authorization/example record is unavailable.")
    return load(path)


def binding(record):
    """Approval binds workflow attribution, corrections and stored source proof."""
    return digest({key: record.get(key) for key in ("id", "case", "field", "doc_type", "document", "read_raw", "read_normalized", "final_value",
                   "outcome", "how", "software", "by", "role", "at", "undone", "observation", "source_provenance")})


def _active_accounts(home):
    _safe(home / "data" / "review_users.json")
    return {user["email"]: user for user in Accounts(home / "data" / "review_users.json").users()
            if user.get("active") and user.get("role") in {"attorney", "paralegal"}}


def _authorization(home, corpus_root, path, actor, cases):
    path = _safe(path)
    grant_root = home / "data" / "evaluation-authorizations" / cases[0]
    if path.parent != grant_root or path.suffix != ".json":
        raise ValueError("Use this firm's retained evaluation authorization record.")
    grant = _read(path)
    required = {"version", "firm_root", "corpus_root", "cases", "approved", "approved_by", "approved_at", "purpose",
                "allowed_examples", "example_bindings_sha256", "approval_evidence", "revoked"}
    if not isinstance(grant, dict) or set(grant) != required or grant["version"] != 1 or grant["approved"] is not True or grant["revoked"] is not False or grant["purpose"] != "evaluation":
        raise ValueError("Current explicit evaluation-only authorization is required.")
    if Path(grant["firm_root"]).absolute() != home or Path(grant["corpus_root"]).absolute() != corpus_root:
        raise ValueError("The authorization belongs to another firm/output scope.")
    allowed = grant["cases"]
    if allowed != list(cases):
        raise ValueError("Case selection exceeds the explicit authorization.")
    users = _active_accounts(home)
    if actor not in users or grant["approved_by"] not in users or users[grant["approved_by"]]["role"] != "attorney":
        raise ValueError("An active own-firm requesting actor and attorney approval are required.")
    approved_at = clock.parse(grant["approved_at"])
    if approved_at is None or approved_at > clock.utcnow():
        raise ValueError("A valid nonfuture approval timestamp is required.")
    proof = grant["approval_evidence"]
    if not isinstance(proof, dict) or set(proof) != {"file", "sha256"} or not isinstance(proof["file"], str) or not re.fullmatch(r"evidence/[a-z0-9_-]{1,64}\.json", proof["file"]):
        raise ValueError("A structured retained data-use approval is required.")
    evidence_path = grant_root / proof["file"]
    evidence = _read(evidence_path)
    if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != proof["sha256"]:
        raise ValueError("The retained approval evidence changed.")
    body = {key: value for key, value in grant.items() if key != "approval_evidence"}
    if evidence != {"version": 1, "purpose": "evaluation", "approved": True, "approved_by": grant["approved_by"],
                    "approved_at": grant["approved_at"], "authorization_sha256": digest(body)}:
        raise ValueError("The retained approval does not bind this exact data-use scope.")
    approved = grant["allowed_examples"]
    if not isinstance(approved, list) or not approved or len(approved) > 1000:
        raise ValueError("A bounded explicit example selection is required.")
    seen = set()
    for row in approved:
        if not isinstance(row, dict) or set(row) != {"case", "id", "binding_sha256"} or row["case"] not in allowed or not isinstance(row["id"], str) or not EXAMPLE.fullmatch(row["id"]) or not isinstance(row["binding_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["binding_sha256"]):
            raise ValueError("The approved example/source selection is malformed.")
        key = (row["case"], row["id"])
        if key in seen:
            raise ValueError("Duplicate approved example IDs are refused.")
        seen.add(key)
    if grant["example_bindings_sha256"] != digest(approved):
        raise ValueError("The approved example selection changed.")
    for case in cases:
        case_dir = _safe(home / "data" / "clients" / case)
        if not case_dir.is_dir() or not restricted.visible_to(users[actor], case_dir):
            raise PermissionError("Current own-case access is required for every selected case.")
        from client_file import allowed as lifecycle_allowed
        lifecycle_allowed(case_dir)  # Initial and final authorization both honor Q1/destruction holds.
    return grant, users[actor]


def export_candidates(firm_root, corpus_root, authorization, actor, cases, out):
    """New own-firm references only. A missing authorization never falls back."""
    home, corpus_root, out = _safe(firm_root), _safe(corpus_root), _safe(out)
    if not isinstance(cases, (list, tuple)) or len(cases) != 1 or not isinstance(cases[0], str) or not CASE.fullmatch(cases[0]):
        raise ValueError("Select exactly one explicit own-case ID per authorization/export; no client-folder scan is permitted.")
    if corpus_root != home / "data" / "evaluation-candidates" or out.parent != corpus_root / cases[0] or not re.fullmatch(r"[a-z0-9_-]{1,64}\.json", out.name):
        raise ValueError("Candidate output must be in the approved own-firm evaluation-candidates scope.")
    if out.exists():
        raise FileExistsError("Candidate output must be new.")
    from portal.communication_consent import Scope, gate
    scope = Scope(home, home / "data" / "portal", home / "data" / "clients")
    with gate(scope):
        grant, user = _authorization(home, corpus_root, authorization, actor, cases)
    rows, files, sources = [], [], []
    changed, selected_bytes = 0, 0
    for approved in grant["allowed_examples"]:
        if approved["case"] not in cases:
            continue
        relative = f"data/reader_examples/{approved['case']}/{approved['id']}.json"
        path = home / relative
        _safe(path)
        selected_bytes += path.stat().st_size
        if selected_bytes > 16 * 1024 * 1024:
            raise ValueError("Select a smaller batch: candidate observation records exceed 16 MiB.")
        record = _read(path)
        if not isinstance(record, dict) or record.get("id") != approved["id"] or record.get("case") != approved["case"] or record.get("undone") or binding(record) != approved["binding_sha256"]:
            raise ValueError("A selected workflow observation changed or was withdrawn.")
        provenance = record.get("source_provenance") or {}
        manifest = provenance.get("reader_manifest")
        if provenance.get("state") != "verified" or not isinstance(manifest, dict) or not manifest or provenance.get("reader_manifest_sha256") != digest(manifest):
            raise ValueError("Candidate reader comparisons require complete recorded source/reader bindings; legacy/missing configuration stays held.")
        from review.evidence import snapshot
        source = snapshot(home / "data" / "clients" / approved["case"], record["document"], provenance["source_sha256"])
        location = source.location(None, provenance["instance_id"])
        if location["state"] != "current" or location["instance_id"] != provenance["instance_id"]:
            raise ValueError("The approved source instance changed.")
        part = next((p for p in (source.plan or {}).get("instances", []) if p.get("instance_id") == provenance["instance_id"]), {})
        if part.get("evidence_fingerprint") != provenance.get("boundary_fingerprint"):
            raise ValueError("The approved source boundary changed.")
        sources.append((record["case"], record["document"].split("#p", 1)[0], provenance["source_sha256"],
                        provenance["instance_id"], provenance["original_range"], provenance["boundary_fingerprint"]))
        changed += record.get("outcome") == "changed"
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append((path, sha))
        rows.append({"case": record["case"], "example_id": record["id"], "example_file": relative,
                     "example_sha256": sha, "binding_sha256": approved["binding_sha256"],
                     "source_provenance": provenance, "reviewer": {k: record.get(k) for k in ("by", "role", "at")},
                     "outcome": record.get("outcome"), "adjudication": "pending"})
    if not rows:
        raise ValueError("No explicitly approved examples were selected.")
    with gate(scope):
        final_grant, user = _authorization(home, corpus_root, authorization, actor, cases)
        if digest(final_grant) != digest(grant):
            raise ValueError("Evaluation data-use authorization changed during export.")
        if any(hashlib.sha256(_safe(path).read_bytes()).hexdigest() != sha for path, sha in files):
            raise ValueError("Selected observation bytes changed during export.")
        final_sources = {}
        for cid, document, sha, instance, original_range, boundary in sources:
            key = (cid, document, sha)
            if key not in final_sources:
                source = snapshot(home / "data" / "clients" / cid, document, sha)
                final_sources[key] = replace(source, data=b"")  # dedupe validation without retaining source image bytes
            source = final_sources[key]
            location = source.location(None, instance)
            part = next((p for p in (source.plan or {}).get("instances", []) if p.get("instance_id") == instance), {})
            if location["state"] != "current" or location.get("instance_id") != instance or location.get("original_range") != original_range or part.get("evidence_fingerprint") != boundary:
                raise ValueError("The approved original instance/range/boundary changed during export.")
        result = {"version": 1, "kind": "evaluation_candidate_references", "approved": False,
                  "independently_adjudicated": False, "training_authorized": False, "created_at": clock.stamp(),
                  "firm_root": str(home), "actor": {"email": actor, "role": user["role"]},
                  "authorization_sha256": digest(grant), "candidates": rows,
                  "intervention": {"unit": "approved_reader_source_field_observation", "numerator": changed,
                                   "denominator": len(rows), "rate": changed / len(rows), "staff_seconds": None},
                  "measurement": {"framework": "tools/evaluate_corpus.py", "human_time_input": "observations.labor human_observed active_minutes with named observer",
                                  "labor": None, "wrong_person": None, "false_split_merge": None, "critical_field_error": None,
                                  "reason": "No independently adjudicated corpus/active-human ledger supplied; these workflow candidates are not measured accuracy or labor savings."}}
        _safe(out)
        # Only newly created own-artifact directories/files receive private
        # modes. Existing firm directories and security settings are untouched.
        for folder in (corpus_root, out.parent):
            if not folder.exists():
                folder.mkdir(mode=0o700)
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(result) + b"\n")
        return result
