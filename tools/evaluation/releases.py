"""Frozen release proposals. No automatic approval, training or promotion."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from .corpus import digest

THRESHOLDS = {"minimum_precision", "minimum_coverage", "maximum_wrong_person", "maximum_critical_errors", "minimum_scored_fills"}


def validate_thresholds(thresholds, owner):
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("named policy owner required")
    if not isinstance(thresholds, dict) or set(thresholds) != THRESHOLDS or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in thresholds.values()):
        raise ValueError("all explicit finite release thresholds required")
    if any(thresholds[k] > 1 for k in ("minimum_precision", "minimum_coverage")):
        raise ValueError("rate thresholds must be 0..1")


def freeze_policy(manifest, thresholds, owner, command):
    validate_thresholds(thresholds, owner)
    return {"version": 1, "state": "frozen_proposal", "owner": owner.strip(), "frozen_at": datetime.now(timezone.utc).isoformat(),
            "corpus_digest": digest(manifest), "thresholds": dict(thresholds), "command": list(command), "approved": False,
            "approval_policy": "Named firm release owner and attorney review after evidence; this tool never records their approval or promotes a reader."}


def _time(text):
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError("release evidence timestamps require timezone")
    return value


def compare(incumbent, challenger, incumbent_run, challenger_run, policy):
    for report, run in ((incumbent, incumbent_run), (challenger, challenger_run)):
        if not isinstance(report, dict) or not isinstance(run, dict) or run.get("configuration_digest") != digest(run.get("configuration")) or run.get("corpus_digest") != policy.get("corpus_digest"):
            raise ValueError("run actual configuration/corpus digest differs")
        if report.get("corpus_digest") != policy.get("corpus_digest") or report.get("observations_digest") != run.get("observations_digest") or report.get("configuration_digest") != run.get("configuration_digest"):
            raise ValueError("release evidence corpus/configuration/observations do not match")
        if report.get("partition") != "evaluation":
            raise ValueError("release comparison requires held-out evaluation partition")
        outcomes = report.get("reader_outcomes") or {}
        counts = outcomes.get("counts")
        if not isinstance(counts, dict) or set(counts) - {"completed", "failed"} or any(type(n) is not int or n < 0 for n in counts.values()) or outcomes.get("cases_without_outcome") != 0 or sum(counts.values()) != report.get("cohort", {}).get("cases"):
            raise ValueError("release reader outcome counts are malformed or incomplete")
    if incumbent["critical_inventory_digest"] != challenger["critical_inventory_digest"]:
        raise ValueError("release critical inventories differ")
    if policy.get("version") != 1 or policy.get("state") != "frozen_proposal" or not policy.get("owner"):
        raise ValueError("frozen named-owner policy required")
    thresholds = policy.get("thresholds") or {}
    validate_thresholds(thresholds, policy.get("owner"))
    blockers = []
    if challenger["critical_inventory_state"] != "accepted":
        blockers.append("EV3 critical inventory is pending; candidate acceptance requires its accepted inventory")
    for label, run in (("incumbent", incumbent_run), ("challenger", challenger_run)):
        if run.get("kind") != "local_pipeline_execution":
            blockers.append(label + " observations are imported/declared-only, not reader execution by this tool")
        if not run.get("started_at") or _time(policy["frozen_at"]) > _time(run["started_at"]):
            blockers.append(label + " run predates policy or has no verified run start; untouched-evaluation policy timing is unproven")
    extraction = challenger["extraction"]["proposal"]
    checks = {"precision": extraction["precision"]["rate"] is not None and extraction["precision"]["rate"] >= thresholds["minimum_precision"],
              "coverage": extraction["coverage"]["rate"] is not None and extraction["coverage"]["rate"] >= thresholds["minimum_coverage"],
              "wrong_person": extraction["counts"]["wrong_person"] <= thresholds["maximum_wrong_person"],
              "critical_errors": extraction["counts"]["critical_errors"] <= thresholds["maximum_critical_errors"],
              "sample_count": extraction["precision"]["denominator"] >= thresholds["minimum_scored_fills"]}
    if challenger["reader_outcomes"]["counts"].get("failed", 0) or challenger["reader_outcomes"]["cases_without_outcome"]:
        blockers.append("failed/unobserved reader cases require explicit investigation; they remain in the cohort")
    proposal_comparison = {}
    for metric in ("precision", "coverage", "wrong_person", "critical_errors"):
        before = incumbent["extraction"]["proposal"][metric]
        after = extraction[metric]
        proposal_comparison[metric] = {"incumbent": before, "challenger": after,
            "delta": {"numerator": after["numerator"] - before["numerator"],
                      "denominator": after["denominator"] - before["denominator"],
                      "rate": after["rate"] - before["rate"] if after["rate"] is not None and before["rate"] is not None else None}}
    return {"version": 1, "policy_digest": digest(policy), "corpus_digest": policy["corpus_digest"], "owner": policy["owner"],
            "incumbent": {"report_digest": digest(incumbent), "run_digest": digest(incumbent_run), "configuration_digest": incumbent_run["configuration_digest"], "provenance_kind": incumbent_run["kind"]},
            "challenger": {"report_digest": digest(challenger), "run_digest": digest(challenger_run), "configuration_digest": challenger_run["configuration_digest"], "provenance_kind": challenger_run["kind"]},
            "proposal_comparison": proposal_comparison, "comparison_note": "Same frozen corpus/inventory; descriptive deltas retain changing denominators and null rates. No automatic regression policy or statistical claim.",
            "checks": checks, "blockers": blockers, "proposal": "blocked" if blockers else "accept" if all(checks.values()) else "reject", "approved": False,
            "warning": "A proposal is not approval. Synthetic evidence is not production accuracy. No model is trained or promoted."}


def decision(comparison, owner, reason, rollback_target):
    if not all(isinstance(value, str) and value.strip() for value in (owner, reason, rollback_target)):
        raise ValueError("decision draft requires named owner, reason and rollback target")
    return {"version": 1, "state": "draft", "comparison_digest": digest(comparison), "policy_digest": comparison["policy_digest"],
            "corpus_digest": comparison["corpus_digest"], "evidence": {"incumbent": comparison["incumbent"], "challenger": comparison["challenger"]},
            "owner": owner.strip(), "proposal": comparison["proposal"], "reason": reason.strip(), "rollback_target": rollback_target.strip(), "approved": False}
