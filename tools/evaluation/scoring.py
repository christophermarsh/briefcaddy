"""Distinct-fact scoring, separate review/PDF stages, explicit denominators."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from .corpus import digest, fact_id, independently_scored, canonical, HASH

STATES = {"filled", "held", "abstained", "not_attempted"}
TIME_STAGES = {"preparation", "correction", "review", "assembly", "attorney", "support"}


def rate(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator, "rate": numerator / denominator if denominator else None}


def _metrics(counts):
    names = {"correct", "wrong", "unsupported", "missing_expected", "held", "abstained", "not_attempted", "no_observation", "unknown_references", "disputed_references", "unscored_references", "unscored_fills", "attribution_unscored_fills", "expected_supported", "independently_referenced", "held_expected", "abstained_expected", "justified_hold", "filled_all", "wrong_person", "subject_checked", "critical_errors", "critical_scored_fills", "source_available", "source_declared", "source_audited", "source_audit_correct"}
    return {"counts": {key: counts[key] for key in sorted(names | set(counts))},
            "precision": rate(counts["correct"], counts["correct"] + counts["wrong"] + counts["unsupported"]),
            "coverage": rate(counts["correct"], counts["expected_supported"]),
            "held": rate(counts["held_expected"], counts["expected_supported"]),
            "explicit_abstention": rate(counts["abstained_expected"], counts["expected_supported"]),
            "safe_abstention": rate(counts["justified_hold"], counts["independently_referenced"]),
            "wrong_person": rate(counts["wrong_person"], counts["subject_checked"]),
            "critical_errors": rate(counts["critical_errors"], counts["critical_scored_fills"]),
            "source_location_available": rate(counts["source_available"], counts["filled_all"]),
            "source_location_declared": rate(counts["source_declared"], counts["filled_all"]),
            "source_link_audit_coverage": rate(counts["source_audited"], counts["filled_all"]),
            "source_link_correctness": rate(counts["source_audit_correct"], counts["source_audited"])}


def _reference_strata(case, fact, documents):
    ids = fact.get("documents") or []
    found = [documents[(case, did)] for did in ids if (case, did) in documents]
    return {field: sorted(set(doc[field] for doc in found)) or ["unknown"] for field in ("language", "quality", "type")}


def score(manifest, observations, inventory, partition="evaluation"):
    if not isinstance(observations, dict) or not isinstance(inventory, dict) or partition not in ("calibration", "evaluation"):
        raise ValueError("observation/inventory objects and known partition required")
    if observations.get("version") != 1 or observations.get("corpus_digest") != digest(manifest):
        raise ValueError("observation corpus/version differs")
    if not isinstance(observations.get("facts"), list):
        raise ValueError("observed facts must be a list; missing is not abstention")
    if inventory.get("version") != 1 or inventory.get("state") not in ("pending_ev3", "accepted") or not isinstance(inventory.get("keys"), list):
        raise ValueError("versioned critical inventory required")
    if any(not isinstance(k, str) or not k.strip() for k in inventory["keys"]) or len(set(inventory["keys"])) != len(inventory["keys"]):
        raise ValueError("unique nonblank critical fact keys required")
    if inventory["state"] == "accepted" and (not inventory.get("keys") or not isinstance(inventory.get("inventory_source_digest"), str) or not HASH.fullmatch(inventory["inventory_source_digest"]) or not isinstance(inventory.get("accepted_by"), str) or not inventory["accepted_by"].strip() or not inventory.get("acceptance_evidence")):
        raise ValueError("accepted EV3 inventory requires source digest, owner and acceptance evidence")
    for name in ("outcomes", "pdf_boxes", "labor"):
        if not isinstance(observations.get(name, []), list):
            raise ValueError(name + " must be a list")
    cases = {case["id"]: case for case in manifest["cases"] if case["partition"] == partition}
    all_cases = {case["id"]: case for case in manifest["cases"]}
    outcomes = {}
    for item in observations.get("outcomes") or []:
        if not isinstance(item, dict) or item.get("case") not in all_cases or item.get("state") not in ("completed", "failed") or item["case"] in outcomes:
            raise ValueError("one completed/failed reader outcome per case required")
        outcomes[item["case"]] = item["state"]
    if any(cid not in outcomes for cid in cases):
        raise ValueError("reader outcome missing for cohort case")
    docs = {(case["id"], doc["id"]): doc for case in manifest["cases"] for doc in case["documents"]}
    refs = {fact_id(case["id"], fact): fact for case in cases.values() for fact in case["expected"]}
    rows = {}
    for row in observations["facts"]:
        if not isinstance(row, dict):
            raise ValueError("fact observation must be an object")
        if row.get("case") not in all_cases:
            raise ValueError("observation names unknown case")
        if not isinstance(row.get("subject"), str) or not isinstance(row.get("key"), str):
            raise ValueError("observed subject/key required")
        fid = fact_id(row["case"], row)
        for stage in ("proposal", "accepted"):
            observed = row.get(stage)
            if observed is not None and (not isinstance(observed, dict) or observed.get("state") not in STATES or (observed["state"] == "filled" and "value" not in observed)):
                raise ValueError("invalid fact observation stage")
        if fid in rows and canonical(rows[fid]) != canonical(row):
            raise ValueError("conflicting duplicate fact observations")
        rows[fid] = row  # exact duplicates are one fact, never repeated-box credit
    critical = set(inventory["keys"])
    reports = {}
    for stage in ("proposal", "accepted"):
        total = Counter()
        strata = defaultdict(Counter)
        for fid in sorted(set(refs) | {fid for fid in rows if fid[0] in cases}):
            ref, row = refs.get(fid), rows.get(fid)
            observed = row.get(stage) if row else None
            c = Counter()
            label = ref["label"] if ref else "unknown"
            independent = bool(ref and independently_scored(ref))
            c["unknown_references"] += label == "unknown"
            c["disputed_references"] += label == "disputed"
            c["unscored_references"] += not independent
            c["expected_supported"] += independent and label == "supported"
            c["independently_referenced"] += independent
            if observed is None:
                c["no_observation"] += 1
                c["missing_expected"] += independent and label == "supported"
            else:
                state = observed["state"]
                c["held"] += state == "held"
                c["abstained"] += state == "abstained"
                c["not_attempted"] += state == "not_attempted"
                c["held_expected"] += independent and label == "supported" and state == "held"
                c["abstained_expected"] += independent and label == "supported" and state == "abstained"
                c["missing_expected"] += independent and label == "supported" and state == "not_attempted"
                c["justified_hold"] += independent and bool(ref.get("safe_hold")) and state in ("held", "abstained")
                if state == "filled":
                    c["filled_all"] += 1
                    evidence = row.get("evidence") or []
                    if not isinstance(evidence, list):
                        raise ValueError("evidence must be a list")
                    owners = set()
                    source_available, source_declared, audited, correct_link = False, False, False, True
                    for link in evidence:
                        if not isinstance(link, dict) or not isinstance(link.get("audit", {}), dict):
                            raise ValueError("source link/audit must be objects")
                        doc = docs.get((fid[0], link.get("document")))
                        if doc is None:
                            raise ValueError("source link names unknown document")
                        ownership = doc.get("subject_adjudication") or {}
                        # A marriage/birth record has multiple people. Its owner
                        # never supplies the subject of every extracted fact.
                        if doc.get("subject_scope") == "validated_single_subject" and doc.get("subject") and ownership.get("basis") == "independent" and ownership.get("by") and ownership.get("history"):
                            owners.add(doc["subject"])
                        for attribution in doc.get("attributions") or []:
                            a = attribution.get("adjudication") or {}
                            if attribution.get("key") != fid[2] or a.get("basis") != "independent" or not a.get("by") or not a.get("history"):
                                continue
                            if not any(key in attribution for key in ("page", "instance", "raw_value")):
                                continue  # location/specific-source binding required
                            if any(attribution[key] != link.get(key) for key in ("page", "instance", "raw_value") if key in attribution):
                                continue
                            owners.add(attribution["subject"])
                        page = link.get("page")
                        if page is not None and (type(page) is not int or page < 0):
                            raise ValueError("source page must be 0-based nonnegative integer or unknown")
                        if type(page) is int and page >= 0:
                            source_declared = True
                            count = doc.get("page_count")
                            if count is not None and (type(count) is not int or page >= count):
                                raise ValueError("source page outside original file")
                            source_available = source_available or type(count) is int and count > page
                        audit = link.get("audit") or {}
                        if audit.get("basis") == "independent" and audit.get("by") and type(audit.get("correct")) is bool:
                            audited = True
                            correct_link = correct_link and audit["correct"]
                    personal_audit = row.get("attribution_audit") or {}
                    if not isinstance(personal_audit, dict) or personal_audit.get("subject") is not None and personal_audit["subject"] not in cases[fid[0]]["subjects"]:
                        raise ValueError("attribution audit requires known subject")
                    if personal_audit.get("basis") == "independent" and personal_audit.get("by") and personal_audit.get("history") and personal_audit.get("subject"):
                        owners.add(personal_audit["subject"])
                    subject_checked = len(owners) == 1
                    wrong_person = subject_checked and owners != {fid[1]}
                    c["subject_checked"] += subject_checked
                    c["wrong_person"] += wrong_person
                    c["source_available"] += source_available
                    c["source_declared"] += source_declared
                    c["source_audited"] += audited
                    c["source_audit_correct"] += audited and correct_link
                    if not independent or not subject_checked:
                        c["unscored_fills"] += 1
                        c["attribution_unscored_fills"] += not subject_checked
                    elif label == "unsupported":
                        c["unsupported"] += 1
                    elif canonical(observed["value"]) == canonical(ref["value"]) and not wrong_person:
                        c["correct"] += 1
                    else:
                        c["wrong"] += 1
                    if independent and subject_checked and fid[2] in critical:
                        c["critical_scored_fills"] += 1
                        c["critical_errors"] += bool(c["wrong"] or c["unsupported"])
            total.update(c)
            grouping = _reference_strata(fid[0], ref or {}, docs)
            for field, labels in grouping.items():
                for value in labels:
                    strata[(field, value)].update(c)
        reports[stage] = {**_metrics(total), "strata": [{"dimension": dimension, "value": value, **_metrics(c)} for (dimension, value), c in sorted(strata.items())]}
    # Final PDF boxes are scored separately, with distinct-fact grouping visible.
    observed_boxes = {}
    for box in observations.get("pdf_boxes") or []:
        if not isinstance(box, dict):
            raise ValueError("PDF observation must be an object")
        key = (box.get("case"), box.get("id"))
        if key[0] not in all_cases or not isinstance(key[1], str) or "value" not in box or key in observed_boxes:
            raise ValueError("unique PDF box/case/value required")
        observed_boxes[key] = box
    pdf = Counter(); pdf_groups = defaultdict(Counter)
    for case in cases.values():
        seen = set()
        for ref in case.get("pdf_boxes") or []:
            if ref.get("id") in seen:
                raise ValueError("duplicate PDF reference box")
            seen.add(ref.get("id"))
            group = (case["id"], ref["subject"], ref["key"], ref.get("instance") or "")
            a = ref.get("adjudication") or {}
            if a.get("basis") != "independent" or not a.get("by") or not a.get("history"):
                pdf["unscored"] += 1
                continue
            pdf["scored_boxes"] += 1
            box = observed_boxes.get((case["id"], ref["id"]))
            outcome = "missing" if box is None else "correct" if canonical(box["value"]) == canonical(ref["value"]) else "wrong"
            pdf[outcome] += 1; pdf_groups[group][outcome] += 1
    pdf["fact_groups"] = len(pdf_groups)
    pdf["groups_with_error_or_missing"] = sum(bool(c["wrong"] or c["missing"]) for c in pdf_groups.values())
    # Active human minutes include every cohort state; runtime is never staff time.
    minutes = Counter(); participants = set(); labor_cases = set()
    for entry in observations.get("labor") or []:
        if not isinstance(entry, dict):
            raise ValueError("labor observation must be an object")
        if entry.get("case") not in all_cases:
            raise ValueError("labor names unknown case")
        if entry["case"] not in cases:
            continue
        if entry.get("stage") not in TIME_STAGES or type(entry.get("active_minutes")) not in (int, float) or not math.isfinite(entry["active_minutes"]) or entry["active_minutes"] < 0:
            raise ValueError("human active time stage/minutes invalid")
        if entry.get("basis") != "human_observed" or not entry.get("observer"):
            raise ValueError("automated runtime is not human active labor")
        minutes[entry["stage"]] += entry["active_minutes"]
        labor_cases.add(entry["case"]); participants.add(entry["observer"])
    cohort = Counter(case["completion"] for case in cases.values())
    return {"version": 1, "corpus_digest": digest(manifest), "observations_digest": digest(observations), "configuration_digest": observations.get("configuration_digest"),
            "critical_inventory_digest": digest(inventory), "critical_inventory_state": inventory["state"], "partition": partition, "synthetic_only": manifest["synthetic_only"],
            "cohort": {"cases": len(cases), "completion": dict(cohort)}, "extraction": reports,
            "pdf": {"counts": dict(pdf), "fidelity": rate(pdf["correct"], pdf["scored_boxes"]), "distinct_fact_group_error": rate(pdf["groups_with_error_or_missing"], pdf["fact_groups"])},
            "reader_outcomes": {"counts": dict(Counter(value for cid, value in outcomes.items() if cid in cases)), "cases_without_outcome": 0},
            "labor": {"active_minutes": dict(minutes), "cases_observed": len(labor_cases), "cases_without_labor_observation": len(cases) - len(labor_cases),
                      "observers": len(participants), "observed_minutes_per_accepted_case": rate(sum(minutes.values()), cohort["accepted"]),
                      "total_minutes_per_accepted_case": {"numerator": sum(minutes.values()), "denominator": cohort["accepted"], "rate": sum(minutes.values()) / cohort["accepted"] if cohort["accepted"] and len(labor_cases) == len(cases) else None},
                      "synthetic_runtime_is_labor": False}}
