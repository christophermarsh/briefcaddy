"""Ephemeral current-I-485 output lineage for reviewer explanations.

This describes the current graph/catalog. It neither approves a source nor
predicts another filing's mapping or a future policy.
"""
from collections import deque

from extract.names import fold_name

CITY = "applicant.birth_city"
STATE = "applicant.birth_state"
REFERENCE = "applicant.marriage_cert_birthplace"
MAX_FACTS = 10000
MAX_EDGES = 50000
MAX_REACHABLE = 512


def output_impact(graph, key, catalog):
    """Follow both rule and normalized-source dependencies to actual outputs.

    A malformed or bounded-out graph is unavailable, never reference-only.
    Missing upstream nodes cannot themselves establish an output relation;
    the query must name a present fact. Catalog history feeds stay relevant.
    """
    base = {"scope": "current_i485", "targets": []}
    unavailable = base | {"state": "unavailable", "reason": "Current I-485 output lineage is unavailable."}
    try:
        facts = graph.all_facts()
        if not isinstance(facts, dict) or not facts or len(facts) > MAX_FACTS or key not in facts:
            return unavailable
        children = {}
        edge_count = 0
        for child, fact in facts.items():
            if not isinstance(child, str) or not child or not isinstance(fact.derived_from, list) or not isinstance(fact.sources, list):
                return unavailable
            parents = list(fact.derived_from)
            for source in fact.sources:
                if not isinstance(source.from_facts, list):
                    return unavailable
                parents.extend(source.from_facts)
            if any(not isinstance(parent, str) or not parent for parent in parents):
                return unavailable
            for parent in set(parents):
                children.setdefault(parent, set()).add(child)
                edge_count += 1
                if edge_count > MAX_EDGES:
                    return unavailable
        paths = {key: [key]}
        queue = deque([key])
        while queue:
            parent = queue.popleft()
            for child in sorted(children.get(parent, ())):
                if child not in paths:
                    paths[child] = paths[parent] + [child]
                    queue.append(child)
                    if len(paths) > MAX_REACHABLE:
                        return unavailable
        # Topological count catches every cycle in the reachable subgraph,
        # including cross-branch cycles not present on the chosen shortest path.
        incoming = {node: 0 for node in paths}
        for parent in paths:
            for child in children.get(parent, ()):
                if child in incoming:
                    incoming[child] += 1
        ready = deque(node for node, count in incoming.items() if count == 0)
        visited = 0
        while ready:
            parent = ready.popleft()
            visited += 1
            for child in children.get(parent, ()):
                if child in incoming:
                    incoming[child] -= 1
                    if incoming[child] == 0:
                        ready.append(child)
        if visited != len(paths):
            return unavailable
        targets = []
        for target, lineage in sorted(paths.items()):
            fields = catalog._fields(target)
            spec = catalog.input(target)
            feeds = spec.get("feeds")
            if fields or (spec.get("on_form") is True and isinstance(feeds, str) and feeds.strip()):
                targets.append({"key": target, "fields": fields, "feeds": feeds,
                                "label": catalog.label(target), "ref": catalog.ref(target),
                                "value": facts[target].value, "lineage": lineage})
        return base | {"state": "mapped" if targets else "reference", "targets": targets,
                       "reason": "Current I-485 output or continuation depends on this fact." if targets else
                                 "No current I-485 field or continuation depends on this fact in the recorded graph."}
    except (AttributeError, KeyError, TypeError, ValueError):
        return unavailable


def birthplace_comparison(graph, catalog):
    """Explain the existing birth/marriage crosscheck without changing facts."""
    city = graph.get(CITY)
    reference = graph.get(REFERENCE)
    state = graph.get(STATE)
    if city is None or reference is None or not isinstance(city.value, str) or not isinstance(reference.value, str):
        return None
    city_value, reference_value = city.value.strip(), reference.value.strip()
    other_city = reference_value.split(",", 1)[0].strip()
    if not city_value or not other_city or fold_name(other_city) == fold_name(city_value):
        return None
    impact = output_impact(graph, REFERENCE, catalog)
    city_impact = output_impact(graph, CITY, catalog)
    target = next((entry for entry in city_impact["targets"] if entry["key"] == CITY and entry["fields"]), None)
    if target is None:
        return {"kind": "unavailable", "scope": "current_i485", "reference_impact": impact,
                "reason": "The current I-485 city field cannot be established."}
    state_value = state.value if state and isinstance(state.value, str) else ""
    specificity = bool(state_value and fold_name(other_city) == fold_name(state_value))
    return {"kind": "specificity" if specificity else "disagreement", "scope": "current_i485",
            "reference_key": REFERENCE, "reference_value": reference_value,
            "reference_impact": impact, "filed_city": target, "birth_state": state_value,
            "alternatives": [city_value, other_city] if not specificity else [],
            "alternative_labels": {city_value: "Current I-485 city: " + city_value,
                                   other_city: "Marriage certificate: " + other_city}}
