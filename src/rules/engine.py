"""Rule engine -- the rule graph half of docs/GRAPH_MODEL.md's "why two
graphs" section. Built once, shared across all 1800 clients: this module
topologically orders a fixed set of Rule objects by their declared
inputs/outputs and runs each one against a single client's FactGraph,
never guessing when an input hasn't resolved.

Every Rule instance here is the *executable* form of a reviewable artifact
that lives in docs/rules/<RULE-ID>.md and needs attorney sign-off before
running at volume -- see the "Rule sign-off" open item in
docs/decisions.md. This engine can be exercised in tests against a rule's
own written logic; running it is not itself the sign-off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from factgraph import FactGraph

RuleFn = Callable[[FactGraph], dict[str, Any]]


@dataclass
class Rule:
    rule_id: str
    inputs: list[str]
    outputs: list[str]
    apply: RuleFn
    # Fact keys that must NOT be present (any status other than "missing"
    # counts as present) for this rule to fire -- e.g. OVERSTAY-01's
    # "no interim status-extension document exists" condition.
    requires_absent: list[str] = field(default_factory=list)
    # What the attorney reads before approving the rule for every case: one or
    # two sentences copied from the rule's docs/rules/<RULE-ID>.md (or its
    # docs/decisions.md entry), and where the rule comes from. Never a new
    # legal statement: a reworded text is listed in docs/attorney_review.md.
    plain_text: str = ""
    source: str = ""
    # The statute a rule rests on, when the product holds its text: how it is cited ("INA 245(h)"), the words of the law as read, where they
    # were read (a URL) and when (YYYY-MM-DD). A suggested Part 14 explanation cites the law only from a rule that holds all four
    # (src/part14_explain.py); with any of them empty the sentence goes without a citation and the card says the attorney adds it.
    # No rule holds a statute's text yet.
    statute_cite: str = ""
    statute_text: str = ""
    statute_source: str = ""
    statute_read: str = ""


def _resolved_tier12(graph: FactGraph, fact_key: str) -> bool:
    fact = graph.get(fact_key)
    return fact is not None and fact.status == "resolved" and fact.tier in (1, 2)


def _present(graph: FactGraph, fact_key: str) -> bool:
    fact = graph.get(fact_key)
    return fact is not None and fact.status != "missing"


def topological_order(rules: list[Rule]) -> list[Rule]:
    """Orders rules so a rule producing another rule's input runs first --
    an output of one rule can be an input to another (docs/GRAPH_MODEL.md).
    Raises ValueError on a cycle; an attorney-reviewed rule set should
    never have one, but this checks rather than silently looping forever."""
    producer: dict[str, str] = {}
    for rule in rules:
        for output in rule.outputs:
            producer[output] = rule.rule_id

    by_id = {rule.rule_id: rule for rule in rules}
    depends_on: dict[str, set[str]] = {rule.rule_id: set() for rule in rules}
    for rule in rules:
        for input_key in rule.inputs:
            dep = producer.get(input_key)
            if dep is not None and dep != rule.rule_id:
                depends_on[rule.rule_id].add(dep)

    ordered: list[Rule] = []
    done: set[str] = set()
    in_progress: set[str] = set()

    def visit(rule_id: str) -> None:
        if rule_id in done:
            return
        if rule_id in in_progress:
            raise ValueError(f"cycle detected in rule graph at rule {rule_id!r}")
        in_progress.add(rule_id)
        for dep in depends_on[rule_id]:
            visit(dep)
        in_progress.discard(rule_id)
        done.add(rule_id)
        ordered.append(by_id[rule_id])

    for rule in rules:
        visit(rule.rule_id)

    return ordered


def run_rules(graph: FactGraph, rules: list[Rule]) -> None:
    """Runs every rule against graph, in dependency order. A rule whose
    inputs never resolve (at tier 1 or 2), or whose requires_absent
    condition isn't met, simply never fires -- its output fact keys are
    left untouched and fall through to Tier 3 in the flag report like any
    other undetermined fact (docs/GRAPH_MODEL.md). No rule is allowed to
    guess with partial inputs."""
    for rule in topological_order(rules):
        if not all(_resolved_tier12(graph, key) for key in rule.inputs):
            continue
        if any(_present(graph, key) for key in rule.requires_absent):
            continue
        outputs = rule.apply(graph)
        for fact_key, value in outputs.items():
            graph.add_derived(fact_key, value, rule.rule_id, list(rule.inputs))
