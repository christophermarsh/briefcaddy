"""Versioned shadow proposals. Human evidence decisions remain authoritative."""
from __future__ import annotations

import hashlib
import json
import re

POLICY = "review-shadow-1"
AUTOMATIC_ACCEPTANCE = False
GATES = {"minimum_held_out_cases": 100, "maximum_false_splits": 0, "maximum_false_merges": 0,
         "maximum_wrong_person": 0, "independent_review_required": True,
         "approved_rollout_required": True, "rollback": "disable acceptance; retain human decisions and original pages"}


def _fold(value):
    from extract.names import fold_name
    return fold_name(str(value or ""))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _identifier(value):
    return re.sub(r"[\s-]+", "", str(value or "").upper())


def _names(facts):
    parts = {kind: set() for kind in ("given_name", "middle_name", "family_name")}
    names = set()
    for fact in facts:
        key = fact.get("key", "").rsplit(".", 1)[-1]
        value = _fold(fact.get("value"))
        if not value:
            continue
        part = next((kind for kind in parts if key == kind or key.endswith("_" + kind)), None)
        if part:
            parts[part].add(value)
        elif re.search(r"(?:name|surname)$", key):
            names.add(value)
    ambiguous = any(len(values) > 1 for values in parts.values())
    if not ambiguous and parts["given_name"] and parts["family_name"]:
        names.add(" ".join(next(iter(parts[key])) for key in parts if parts[key]))
    return names, ambiguous


def boundary_proposal(plan):
    """Expose source-bound proposed start pages; no source/page is removed."""
    parts = plan.get("instances") or []
    starts = sorted({part["first"] for part in parts if isinstance(part.get("first"), int)})
    evidence = [{"pages": [p.get("first"), p.get("last")], "type": p.get("type"),
                 "reasons": p.get("reasons", []), "instance_id": p.get("instance_id")} for p in parts]
    complete = bool(starts and starts[0] == 0 and plan.get("source_sha256") and not plan.get("stale")
                    and not plan.get("processing_incomplete"))
    return {"policy": POLICY, "mode": "shadow", "automatic_acceptance": False,
            "state": "exception" if plan.get("held") or not complete else "proposed",
            "source_sha256": plan.get("source_sha256"), "source_version": plan.get("fingerprint"),
            "proposal_id": _digest([POLICY, plan.get("source_sha256"), plan.get("fingerprint"), starts]),
            "proposed_starts": starts, "evidence": evidence,
            "reason": "Review these original start pages; all blank, reverse and continuation pages stay in the original ranges.",
            "gates": GATES}


def subject_proposal(row, people, *, reviewed_sources=()):
    """Names alone suggest a comparison, never an accepted person assignment.

    The adapter supplies only current source-bound reviews from this case.
    Person metadata alone cannot establish an independent identifier.
    """
    proposals = {}
    for slot in row.get("slots", []):
        facts = [fact for fact in row.get("facts", []) if fact.get("role") == slot]
        names, ambiguous_name_parts = _names(facts)
        identifiers = {}
        for fact in facts:
            kind = fact.get("key", "").rsplit(".", 1)[-1]
            if kind in {"a_number", "passport_number", "ssn", "i94_number"}:
                identifiers.setdefault(kind, set()).add(_identifier(fact.get("value")))
        conflict = ambiguous_name_parts or any(len(values) != 1 or "" in values for values in identifiers.values()) or len(names) > 1
        matches = [p for p in people if p.get("active") is not False and _fold(p.get("label")) in names]
        def independent_identifier(person, kind, values):
            independent_values = set()
            for source in reviewed_sources:
                assignment = source.get("assignment") or {}
                if (not source.get("bound") or not source.get("current") or assignment.get("reference_only")
                        or not assignment.get("who") or assignment.get("role") not in {"paralegal", "attorney"}
                        or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("source_sha256", "")))
                        or source["source_sha256"] == row.get("source_sha256")):
                    continue
                for fact in source.get("facts", []):
                    target = (assignment.get("roles") or {}).get(fact.get("role")) or {}
                    if (target.get("subject_id") == person["id"] and fact.get("state") == "accepted"
                            and fact.get("evidence_version") and fact.get("key", "").rsplit(".", 1)[-1] == kind):
                        independent_values.add(_identifier(fact.get("value")))
            return len(independent_values) == 1 and independent_values == values
        id_matches = [p for p in matches if identifiers and all(
            independent_identifier(p, kind, values) for kind, values in identifiers.items())]
        similar = [p for p in people if p.get("active") is not False and names and any(
            set(_fold(p.get("label")).split()) & set(name.split()) for name in names)]
        unique = row.get("bound") and not conflict and len(id_matches) == 1 and len(matches) == 1 and len(similar) == 1
        proposals[slot] = {"subject_id": id_matches[0]["id"] if unique else None,
                           "state": "proposed" if unique else "exception",
                           "candidate_ids": [p["id"] for p in matches],
                           "reason": "Independent exact name and identifier agree; confirm against original." if unique else
                                     "Conflicting, similar or insufficient independent identity evidence; choose a person against the original.",
                           "evidence_versions": [f.get("evidence_version") for f in facts]}
    return {"policy": POLICY, "mode": "shadow", "automatic_acceptance": False,
            "source_sha256": row.get("source_sha256"), "source_version": row.get("fingerprint"),
            "proposal_id": _digest([POLICY, row.get("source_sha256"), row.get("fingerprint"), proposals]),
            "roles": proposals, "gates": GATES}


def evaluate(rows):
    """Evaluate separately adjudicated held-out labels with error denominators.

    Missing/disputed labels cannot count as correct. Each row has a source/case
    partition and true/proposed start pages and optional true/proposed person.
    Workflow confirmations are not automatically adjudicated evaluation truth.
    """
    counts = {k: 0 for k in ("cases", "families", "sources", "unadjudicated", "false_splits", "false_merges", "wrong_person",
                             "boundary_cases", "person_cases", "person_abstentions", "person_proposals", "person_unadjudicated", "boundary_unadjudicated")}
    seen, cases, families, hashes = set(), set(), set(), set()
    for row in rows:
        for dimension in set(row.get("unadjudicated_dimensions", [])) & {"person", "boundary"}:
            counts[dimension + "_unadjudicated"] += 1
            counts["unadjudicated"] += 1
        proof = row.get("adjudication") or {}
        if (row.get("partition") != "held_out" or proof.get("basis") != "independent" or not proof.get("by")
                or not proof.get("history") or not row.get("family_group") or not row.get("corpus_digest")):
            counts["unadjudicated"] += 1
            continue
        identity = (row.get("case_id"), row.get("source_sha256"))
        if not all(identity) or identity in seen or row["source_sha256"] in hashes:
            raise ValueError("Held-out evaluation requires distinct case/source identities.")
        seen.add(identity)
        hashes.add(row["source_sha256"])
        cases.add(row["case_id"]); families.add(row["family_group"])
        counts["sources"] += 1
        if "true_starts" in row:
            true, proposed = set(row["true_starts"]), set(row.get("proposed_starts", []))
            counts["boundary_cases"] += 1
            counts["false_splits"] += len(proposed - true)
            counts["false_merges"] += len(true - proposed)
        if row.get("true_person") is not None:
            counts["person_cases"] += 1
            if row.get("proposed_person") is None:
                counts["person_abstentions"] += 1
            elif row["proposed_person"] != row["true_person"]:
                counts["person_proposals"] += 1
                counts["wrong_person"] += 1
            else:
                counts["person_proposals"] += 1
    counts.update(cases=len(cases), families=len(families))
    measured = (len(cases) >= GATES["minimum_held_out_cases"] and len(families) >= GATES["minimum_held_out_cases"] and counts["boundary_cases"] >= GATES["minimum_held_out_cases"] and
                counts["person_cases"] >= GATES["minimum_held_out_cases"] and
                counts["person_proposals"] >= GATES["minimum_held_out_cases"] and
                not any(counts[k] for k in ("false_splits", "false_merges", "wrong_person", "unadjudicated")))
    return {"policy": POLICY, "counts": counts, "measurement_gate_met": measured,
            "person_proposal_coverage": {"numerator": counts["person_proposals"], "denominator": counts["person_cases"]},
            "person_abstention": {"numerator": counts["person_abstentions"], "denominator": counts["person_cases"]},
            "automatic_acceptance": False, "rollout": "disabled_pending_independent_review_and_approval", "gates": GATES}
