"""Firm/category policy profiles -- the answers a paralegal fills in the
same way for every client of a filing category, made explicit, gated,
and traceable instead of living in someone's head.

Measured on the real example: of the 232 fields the paralegal filled that
the pipeline couldn't, ~130 were policy, not client data -- SIJS category
and exemption boxes, "NOT APPLICABLE" in fields that don't apply (USCIS's
own form instructions ask for N/A), and "No" on the ~65 Part 9 items the
intake questionnaire never asks.

Each policy (schemas/law/policy_sijs.json) fires only when its conditions
hold and writes Tier 2 derived facts under its own id, so:
  - validate flags every one for attorney sign-off (like any rule);
  - a policy answer that contradicts a document or the client's own
    answer becomes a conflict (FactGraph.add_derived), never an override;
  - a fact that already has an answer is left alone ("unanswered" mode).
Conditions:
  when_present   every listed fact exists and is resolved
  unless_present none of the listed facts exists (any status)
  when_values    listed facts resolved to exactly these values
  max_age        applicant.dob resolved and the applicant is younger than this
Opt-in and unapproved, like every rule here (docs/decisions.md).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from factgraph import FactGraph
import clock


def load_policy_profile(path: str | Path, firm: bool = True) -> list[dict[str, Any]]:
    """The policies the engine uses: the shipped ones, with the firm's own wording and answers over them and a policy the firm
    switched off left out (src/rules/firm_policies.py). firm=False: the shipped file as it is."""
    policies = json.loads(Path(path).read_text(encoding="utf-8"))["policies"]
    if not firm:
        return policies
    from rules import firm_policies

    return firm_policies.apply(policies)


def _resolved(graph: FactGraph, key: str):
    fact = graph.get(key)
    return fact if fact is not None and fact.status == "resolved" else None


def _age(dob_iso: str, today: date) -> int:
    dob = date.fromisoformat(dob_iso)
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def conditions_hold(graph: FactGraph, policy: dict[str, Any], today: date) -> bool:
    if any(_resolved(graph, k) is None for k in policy.get("when_present", [])):
        return False
    if any(graph.get(k) is not None for k in policy.get("unless_present", [])):
        return False
    for key, value in policy.get("when_values", {}).items():
        fact = _resolved(graph, key)
        if fact is None or fact.value != value:
            return False
    if "max_age" in policy:
        dob = _resolved(graph, "applicant.dob")
        if dob is None or _age(dob.value, today) >= policy["max_age"]:
            return False
    return True


def run_policies(graph: FactGraph, policies: list[dict[str, Any]], today: date | None = None) -> list[str]:
    """Applies every policy whose conditions hold; returns the ids applied."""
    today = today or clock.today()
    applied = []
    for policy in policies:
        if not conditions_hold(graph, policy, today):
            continue
        inputs = (
            policy.get("when_present", [])
            + list(policy.get("when_values", {}))
            + (["applicant.dob"] if "max_age" in policy else [])
        )
        # Derived from a client self-report (Tier 3) -> stays Tier 3.
        tier = 3 if any((f := graph.get(k)) is not None and f.tier == 3 for k in inputs) else 2
        wrote = False
        for key, value in policy.get("set", {}).items():
            if policy.get("mode") == "unanswered" and graph.get(key) is not None:
                continue
            graph.add_derived(key, value, f"POLICY:{policy['id']}", inputs, tier=tier)
            wrote = True
        if wrote:
            applied.append(policy["id"])
    return applied
