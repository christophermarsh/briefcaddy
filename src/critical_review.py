"""EV3 manual source confirmation, separate from legal/model-release approval."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import re

import clock
from document_instances import digest
import subject_attribution as subjects
import reader_manifest
import schema_path

INVENTORY = schema_path.path("register", "critical_fields")
VERSION = 1


def inventory() -> dict:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def critical(key: str, spec: dict | None = None) -> bool:
    spec = spec or inventory()
    if key in spec["keys"] or any(re.fullmatch(p, key) for p in spec["patterns"]):
        return True
    if key in spec["reference_only_keys"] or any(re.fullmatch(p, key) for p in spec["reference_only_patterns"]):
        return False
    return spec["unknown_key_policy"] == "require_source_review"


def origins(graph, key: str, seen=None) -> list:
    """Follow the exact input edges as well as older key-based derivations."""
    seen = set(seen or ())
    if key in seen:
        return []
    seen.add(key)
    fact = graph.get(key)
    if not fact:
        return []
    found = [s for s in fact.sources if subjects.original_source(s) and not s.from_facts and not s.input_evidence]
    versions = {v for s in fact.sources for v in s.input_evidence}
    if versions:
        found += [s for f in graph.all_facts().values() for s in f.sources if s.evidence_version in versions
                  and not s.from_facts and not s.input_evidence]
    for parent in set(fact.derived_from) | {k for s in fact.sources for k in s.from_facts}:
        found += origins(graph, parent, seen)
    unique = {digest(asdict(s) | {"extracted_at": None}): s for s in found}
    return list(unique.values())


def keys(graph) -> set[str]:
    spec = inventory()
    return {key for key in graph.all_facts() if critical(key, spec) and origins(graph, key)}


def _dependencies(graph, key, seen=None) -> set[str]:
    seen = set(seen or ())
    if key in seen or not graph.get(key):
        return set()
    seen.add(key)
    fact = graph.get(key)
    parents = set(fact.derived_from) | {k for s in fact.sources for k in s.from_facts}
    return parents | {dep for parent in parents for dep in _dependencies(graph, parent, seen)}


def context(case_dir: Path, decisions: dict | None = None) -> dict[str, dict]:
    """Current eligible evidence, without replaying field confirmations.

    Passing an explicit empty decision map avoids load_decisions recursion.
    Subject/boundary guards still run. No data is persisted by this read.
    """
    from review.state import reviewed_graph
    import documents
    import document_instances
    case_dir = Path(case_dir)
    if not (case_dir / "fact_graph.json").exists():
        return {}
    if decisions is None:
        from review.state import load_decisions
        decisions = load_decisions(case_dir)
    decisions = {iid: d for iid, d in decisions.items() if not d.get("undone")}
    graph = reviewed_graph(case_dir, decisions=decisions, g28_card=False)
    data = documents.read(case_dir) or {}
    rows = subjects.views(case_dir)
    by_instance = {row["instance_id"]: row for row in rows}
    plans = {part["instance_id"]: (plan, part) for plan in document_instances.views(case_dir)
             for part in plan.get("instances", []) if part.get("instance_id")}
    records = {alias: r for r in data.get("documents", []) for alias in (r.get("doc_ids") or r.get("files") or [])}
    current = {}
    spec = inventory()
    manifests = {}
    # Replay current input decisions, but do not let a field's own correction
    # replace the evidence/value it was made over. Grouped fields share a replay.
    uncorrected = reviewed_graph(case_dir, decisions={}, g28_card=False)
    replays = {}
    for key in sorted(keys(graph) | keys(uncorrected)):
        own = tuple(sorted(iid for iid, d in decisions.items() if d.get("action") in {"set", "blank"}
                           and key in (d.get("values", {}) if d["action"] == "set" else d.get("item", {}).get("facts", []))))
        if own and own not in replays:
            replays[own] = reviewed_graph(case_dir, decisions={iid: d for iid, d in decisions.items() if iid not in own}, g28_card=False)
        basis = replays[own] if own else graph
        fact = basis.get(key)
        if fact is None or not origins(basis, key):
            continue
        proofs, reasons = [], []
        for source in origins(basis, key):
            row = by_instance.get(source.instance_id)
            plan, part = plans.get(source.instance_id, ({}, {}))
            # Missing retained-byte proof cannot be turned into a field approval.
            if not row or not row["bound"] or not row["current"] or not plan.get("source_sha256"):
                reasons.append("Review document boundaries and whose facts these are in Documents first.")
            if source.doc_type not in manifests:
                manifests[source.doc_type] = reader_manifest.current(source.doc_type)
            record = records.get(source.doc_id, {})
            quality = {k: record.get(k) for k in ("quality", "quality_basis", "quality_set_by")}
            reasons += list(source.reading_issues)
            if quality.get("quality") in {"blurry", "cut_off", "partial", "check", "unknown"}:
                reasons.append("The document quality needs a source check: " + quality["quality"] + ".")
            if source.read_manifest is None:
                reasons.append("The original reader configuration was not recorded; this is a manual source review.")
            proofs.append({"source": asdict(source) | {"extracted_at": None},
                           "source_sha256": plan.get("source_sha256"),
                           "original_zero_based_inclusive_range": [part.get("first"), part.get("last")],
                           "boundary_fingerprint": part.get("evidence_fingerprint"),
                           "subject_assignment": data.get("subject_assignments", {}).get(source.instance_id),
                           "quality": quality, "expected_manifest": manifests[source.doc_type]})
        deps = _dependencies(basis, key) - {key}
        dep_decisions = {iid: {k: d.get(k) for k in ("action", "values", "at", "reviewer")}
                         for iid, d in (decisions or {}).items() if iid not in own and not d.get("undone")
                         and d.get("action") in {"set", "blank"}
                         and deps & set(d.get("item", {}).get("facts", []))}
        body = {"version": VERSION, "key": key, "inventory_source_digest": digest(spec),
                "observed_status": fact.status, "observed_value": fact.value,
                "evidence": sorted(proofs, key=digest), "dependency_decisions": dep_decisions}
        current[key] = {"fingerprint": digest(body), "proof": body, "reasons": sorted(set(reasons)),
                        "bound": all(p["source_sha256"] and p["subject_assignment"] for p in proofs)
                        and not any("boundaries and whose" in r for r in reasons)}
    return current


def _selected(decision: dict, key: str, current: dict):
    if decision["action"] == "blank":
        return None
    if decision["action"] == "set":
        return decision.get("values", {}).get(key)
    return current["proof"]["observed_value"]


def confirmation(case_dir: Path, item: dict, decision: dict, current=None) -> dict | None:
    """Create a named proof only for the values this action actually selects."""
    if decision["action"] not in {"confirm", "set", "blank"}:
        return None
    current = current if current is not None else context(case_dir)
    touched = set(decision.get("values", {})) if decision["action"] == "set" else set(item["facts"])
    touched &= set(current)
    if not touched:
        return None
    if decision.get("role") not in {"paralegal", "attorney"}:
        raise ValueError("Critical source review requires a signed-in staff reviewer.")
    supplied = decision.get("evidence_fingerprints") or {}
    if not isinstance(supplied, dict):
        raise ValueError("Source review fingerprints must be an object.")
    for key in touched:
        if not current[key]["bound"]:
            raise ValueError("Review document boundaries and whose facts these are in Documents first.")
        if supplied.get(key) != current[key]["fingerprint"]:
            raise ValueError("This evidence changed or was not opened for source review. Refresh this item and check the original.")
        if decision["action"] == "confirm" and current[key]["proof"]["observed_status"] != "resolved":
            raise ValueError("Choose or correct the conflicting value before confirming its source.")
        selected = _selected(decision, key, current[key])
        if decision["action"] == "confirm" and selected in (None, ""):
            raise ValueError("This read has no supported value. Correct it from the source or explicitly leave it blank.")
        if selected not in (None, "") and key == "applicant.i94_number":
            from extract.i94 import number_issue
            issue = number_issue(selected)
            if issue:
                raise ValueError(issue)
    return {"version": VERSION, "basis": "manual_retained_source_review", "model_release_approval": False,
            "keys": {key: {"fingerprint": current[key]["fingerprint"], "proof": current[key]["proof"],
                           "chosen_value": _selected(decision, key, current[key])} for key in sorted(touched)}}


def valid(decision: dict, current: dict) -> bool:
    relevant = set(decision.get("values", {})) if decision.get("action") == "set" else set(decision.get("item", {}).get("facts", []))
    relevant &= set(current)
    proof = decision.get("evidence_confirmation")
    if not relevant:
        return not proof  # evidence that disappeared never becomes a typed-only approval
    if (decision.get("action") not in {"confirm", "set", "blank"} or not isinstance(proof, dict) or proof.get("version") != VERSION
            or proof.get("basis") != "manual_retained_source_review" or proof.get("model_release_approval") is not False
            or not str(decision.get("reviewer") or "").strip() or decision.get("role") not in {"paralegal", "attorney"}
            or not isinstance(decision.get("at"), str) or "T" not in decision["at"] or not clock.parse(decision["at"])):
        return False
    if not isinstance(proof.get("keys"), dict) or set(proof["keys"]) != relevant:
        return False
    return all(isinstance(proof["keys"][key], dict) and current[key]["bound"] and proof["keys"][key].get("fingerprint") == current[key]["fingerprint"]
               and proof["keys"][key].get("proof") == current[key]["proof"]
               and proof["keys"][key].get("chosen_value") == _selected(decision, key, current[key]) for key in relevant)


def filter_decisions(case_dir: Path, decisions: dict) -> dict:
    if not decisions:
        return decisions
    # Removal is monotone: a stale input decision cannot keep a dependent
    # confirmation current for even one request. The log itself is untouched.
    while decisions:
        current = context(case_dir, decisions)
        filtered = {iid: d for iid, d in decisions.items() if d.get("action") not in {"confirm", "set", "blank"}
                    or valid(d, current)}
        if len(filtered) == len(decisions):
            return filtered
        decisions = filtered
    return {}


def mark_graph(graph, decisions):
    graph._critical_reviewed = confirmed_keys(decisions)
    graph._critical_notice_holds = {s.doc_id for key in keys(graph) - graph._critical_reviewed for s in origins(graph, key)
                                    if s.doc_type in subjects.NOTICE_TYPES}
    return graph


def confirmed_keys(decisions):
    return {key for d in decisions.values() if isinstance(d.get("evidence_confirmation"), dict)
            and isinstance(d["evidence_confirmation"].get("keys"), dict)
            for key, value in d["evidence_confirmation"]["keys"].items() if isinstance(value, dict)}


def flags(graph):
    from validate.validate import Flag
    accepted = getattr(graph, "_critical_reviewed", set())
    return [Flag("review", key, f"{key}: critical document read. Check the original source and confirm, correct, or leave blank; "
                 "the reader score is not a measured probability of correctness.") for key in sorted(keys(graph) - accepted)]


def problems(case_dir: Path) -> list[str]:
    from review.state import load_decisions
    current = context(case_dir)
    accepted = confirmed_keys(load_decisions(case_dir))
    return [f"Critical source review is still required: {key}." for key in sorted(set(current) - accepted)]


def pending_notices(case_dir: Path, graph) -> list[dict]:
    """Keep staff attention continuous after subject review, before source review."""
    aliases = getattr(graph, "_critical_notice_holds", set())
    labels = {"date": "Notice date", "due": "Printed response date", "appointment": "Printed appointment",
              "valid_from": "Printed validity start", "valid_to": "Printed validity end", "priority_date": "Printed priority date"}
    out = []
    for row in subjects.views(case_dir):
        if not row["bound"] or not row["current"] or not set(row["doc_ids"]) & aliases:
            continue
        dates = [{"label": labels[f["key"].rsplit(".", 1)[-1]], "value": f["value"], "page": f["page"]}
                 for f in row["facts"] if f["state"] == "accepted" and f["key"].startswith("folder.notice.")
                 and f["key"].rsplit(".", 1)[-1] in labels]
        if dates:
            out.append({"instance_id": row["instance_id"], "file": row["file"], "first": row["first"], "last": row["last"],
                        "dates": dates, "state": "source_unconfirmed"})
    return out
