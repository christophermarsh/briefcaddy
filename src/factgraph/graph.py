"""The fact graph: one instance per client. See docs/GRAPH_MODEL.md for
the full design this implements -- nodes are facts, edges are the sources
(documents) that support each fact, and a fact with disagreeing sources is
a conflict, never silently overwritten by "last write wins."

Deliberately NOT a general graph library (networkx etc.) -- the shape here
is bipartite (documents -> facts) and small per client, so a plain dict of
Fact objects is the whole implementation. The rule engine's dependency
ordering (a genuine DAG problem) lives separately in src/rules/engine.py,
not here -- see docs/GRAPH_MODEL.md's "why two graphs" section.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import clock

VALID_TIERS = (1, 2, 3)
VALID_STATUSES = ("resolved", "conflict", "missing")


def _now() -> str:
    return clock.stamp()


@dataclass
class Source:
    """One document's contribution to a fact -- an edge from a document
    node to a fact node."""

    doc_id: str
    doc_type: str
    raw_value: str
    normalized_value: Any
    confidence: float
    extracted_at: str = field(default_factory=_now)
    # the facts a derived value was worked out from (questionnaire.entered_via_border -> applicant.last_arrival_manner):
    # the review screen shows their scans next to it
    from_facts: list = field(default_factory=list)
    # the page (0-based) of the document the raw value was read from, when the batch held the pages: a card opens the paper there
    page: int | None = None
    # Retained evidence identity and printed reader role (EV2). These are
    # provenance, never an accepted ownership/field confirmation flag.
    instance_id: str | None = None
    subject_role: str | None = None
    evidence_version: str | None = None
    input_evidence: list[str] = field(default_factory=list)
    # Actual read-time declared configuration. None means a legacy/unrecorded
    # read, never today's configuration silently attached to old evidence.
    read_manifest: dict | None = None
    reading_issues: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    """Recorded only once a conflict has been resolved -- who/what decided,
    and why. See docs/decisions.md for the human-facing record this often
    traces back to."""

    chosen_value: Any
    reason: str
    resolved_by: str
    resolved_at: str = field(default_factory=_now)


@dataclass
class Fact:
    fact_key: str
    tier: int
    status: str
    value: Any = None
    sources: list[Source] = field(default_factory=list)
    derived_by: str | None = None
    derived_from: list[str] = field(default_factory=list)
    resolution: Resolution | None = None
    missing_reason: str | None = None
    # A human's sign-off in the review app (src/review) -- distinct from
    # `resolution`, which a rule can also write (CITIZENSHIP-01 picks among
    # disagreeing sources) and which must NOT count as human review.
    review: Resolution | None = None


REVIEW_DOC_ID = "paralegal_review"
TYPED_BY_A_PERSON = ("intake_questionnaire", "office_question", "paralegal_review")  # the client's own answers and a reviewer's: Tier 3


class FactGraph:
    """One per client. Facts are added incrementally as extractors and
    rules run; nothing is ever silently overwritten -- a second,
    disagreeing source flips a fact to `conflict` instead of replacing the
    first source's value."""

    def __init__(self, client_id: str):
        self.client_id = client_id
        self._facts: dict[str, Fact] = {}

    def add_source(
        self,
        fact_key: str,
        doc_id: str,
        doc_type: str,
        raw_value: str,
        normalized_value: Any,
        confidence: float,
        tier: int = 1,
        from_facts: list[str] | None = None,
        page: int | None = None,
        instance_id: str | None = None,
        subject_role: str | None = None,
        evidence_version: str | None = None,
        input_evidence: list[str] | None = None,
        read_manifest: dict | None = None,
        reading_issues: list[str] | None = None,
    ) -> Fact:
        """Records a document's (or, with tier=3, a questionnaire's/human's)
        contribution to a fact. Tier defaults to 1 (direct extraction from
        an official document) since that's what every extractor in this
        pipeline currently produces; a Tier 3 fact_key -- decided once per
        field, not inferred from doc_type (docs/ARCHITECTURE.md's
        three-tier table) -- is still recorded through this same method
        with tier=3 explicitly passed by the caller. If this fact already
        has a source with a different normalized_value, the fact becomes a
        conflict -- the new source is still recorded, not discarded, so
        both are visible."""
        if tier not in VALID_TIERS:
            raise ValueError(f"invalid tier: {tier}")
        fact = self._facts.get(fact_key)
        if fact is None:
            fact = Fact(fact_key=fact_key, tier=tier, status="resolved")
            self._facts[fact_key] = fact

        inputs = set(input_evidence or [])
        for parent_key in from_facts or []:
            parent = self._facts.get(parent_key)
            sources = parent.sources if parent else []
            same_document = [s for s in sources if s.doc_id == doc_id]
            for original in same_document or sources:
                inputs.update(original.input_evidence or ([original.evidence_version] if original.evidence_version else []))
        fact.sources.append(
            Source(
                doc_id=doc_id,
                doc_type=doc_type,
                raw_value=raw_value,
                normalized_value=normalized_value,
                confidence=confidence,
                from_facts=list(from_facts or []),
                page=page,
                instance_id=instance_id,
                subject_role=subject_role,
                evidence_version=evidence_version,
                input_evidence=sorted(inputs),
                read_manifest=read_manifest,
                reading_issues=list(reading_issues or []),
            )
        )
        fact.missing_reason = None
        # A client's answer that a document also states is document-backed:
        # the fact takes its best source's tier (a derived fact keeps its rule).
        if fact.derived_by is None:
            fact.tier = min(fact.tier, tier)
        self._recompute(fact)
        return fact

    def _recompute(self, fact: Fact) -> None:
        distinct_values = {s.normalized_value for s in fact.sources}
        if len(distinct_values) <= 1:
            fact.status = "resolved"
            if fact.sources:
                fact.value = fact.sources[0].normalized_value
        else:
            fact.status = "conflict"
            best = max(fact.sources, key=lambda s: s.confidence)
            fact.value = best.normalized_value

    def add_derived(
        self,
        fact_key: str,
        value: Any,
        rule_id: str,
        input_fact_keys: list[str],
        tier: int = 2,
    ) -> Fact:
        """Records a rule's output (Tier 2 by default). Traceable back to
        the specific facts the rule read (`derived_from`), not just "a
        rule ran" -- see RULE OVERSTAY-01 / NAME-01 in docs/GRAPH_MODEL.md."""
        if tier not in VALID_TIERS:
            raise ValueError(f"invalid tier: {tier}")
        existing = self._facts.get(fact_key)
        if existing is not None and existing.sources:
            # Never discard sources (the module's core promise). Found on the
            # real example: OVERSTAY-01's output replaced the client's own
            # questionnaire answer for the same Part 9 item -- they agreed
            # there, but a disagreement would have vanished silently.
            existing.tier = tier
            existing.derived_by = rule_id
            existing.derived_from = list(input_fact_keys)
            existing.value = value
            source_values = {s.normalized_value for s in existing.sources}
            if value in source_values:
                existing.status = "resolved"
                if len(source_values) > 1:  # rule picked among disagreeing sources (e.g. CITIZENSHIP-01)
                    existing.resolution = Resolution(
                        chosen_value=value, reason=f"chosen by rule {rule_id}", resolved_by=rule_id
                    )
            else:
                existing.status = "conflict"  # rule contradicts every source -> a human decides
            return existing
        fact = Fact(
            fact_key=fact_key,
            tier=tier,
            status="resolved",
            value=value,
            derived_by=rule_id,
            derived_from=list(input_fact_keys),
        )
        self._facts[fact_key] = fact
        return fact

    def mark_missing(self, fact_key: str, reason: str, tier: int = 3) -> Fact:
        """Explicitly records that this fact was looked for and not found
        -- distinct from a fact key nobody has checked yet at all. Lets the
        flag report distinguish "we looked, nothing there, ask the client"
        from a fact_key that isn't wired up yet."""
        fact = Fact(fact_key=fact_key, tier=tier, status="missing", missing_reason=reason)
        self._facts[fact_key] = fact
        return fact

    # --- human review (src/review) --------------------------------------
    # Every review action is additive: the pipeline's own sources, rule
    # derivations and resolutions stay on the fact; the reviewer's decision
    # is recorded next to them with who, when and why.

    def sign_off(self, fact_key: str, reviewer: str, note: str = "") -> Fact:
        """The reviewer confirms the fact's current value as-is."""
        fact = self._facts.get(fact_key)
        if fact is None:
            raise KeyError(f"no such fact: {fact_key}")
        if fact.status != "resolved":
            raise ValueError(f"fact {fact_key!r} is {fact.status}; pick or enter a value instead of confirming")
        fact.review = Resolution(chosen_value=fact.value, reason=note or "confirmed in review", resolved_by=reviewer)
        return fact

    def set_by_review(self, fact_key: str, value: Any, reviewer: str, note: str = "") -> Fact:
        """The reviewer supplies the value (corrects it, picks one of
        disagreeing sources, or enters an answer the pipeline couldn't read).
        Recorded as a source of its own, so the audit trail shows both what
        the pipeline had and what the human decided."""
        before = self._facts.get(fact_key)
        old = None if before is None else before.value
        if before is None or before.status != "resolved" or before.value != value:
            self.add_source(fact_key, REVIEW_DOC_ID, "paralegal_review", str(value), value, 1.0, tier=3 if before is None else before.tier)
        fact = self._facts[fact_key]
        reason = note or ("entered in review" if old is None else f"set in review (pipeline had {old!r})")
        if fact.status == "conflict":
            fact.value = value
            fact.status = "resolved"
            fact.resolution = Resolution(chosen_value=value, reason=reason, resolved_by=reviewer)
        fact.value = value
        fact.review = Resolution(chosen_value=value, reason=reason, resolved_by=reviewer)
        return fact

    def blank_by_review(self, fact_key: str, reviewer: str, note: str = "") -> Fact | None:
        """The reviewer decides the field stays empty. The fact keeps its
        sources (for audit) but its value becomes None, which the field
        mapper never writes."""
        fact = self._facts.get(fact_key)
        if fact is None:
            return None
        old = fact.value
        fact.value = None
        fact.status = "resolved"
        fact.review = Resolution(chosen_value=None, reason=note or f"left blank in review (pipeline had {old!r})", resolved_by=reviewer)
        return fact

    def resolve_conflict(self, fact_key: str, chosen_value: Any, reason: str, resolved_by: str) -> Fact:
        fact = self._facts.get(fact_key)
        if fact is None:
            raise KeyError(f"no such fact: {fact_key}")
        if fact.status != "conflict":
            raise ValueError(f"fact {fact_key!r} is not in conflict (status={fact.status!r})")
        fact.value = chosen_value
        fact.status = "resolved"
        fact.resolution = Resolution(chosen_value=chosen_value, reason=reason, resolved_by=resolved_by)
        return fact

    def get(self, fact_key: str) -> Fact | None:
        return self._facts.get(fact_key)

    def all_facts(self) -> dict[str, Fact]:
        return dict(self._facts)

    def replace_from(self, fact_key: str, doc_type: str, doc_id: str, value: Any) -> bool:
        """Swaps a fact that only `doc_type` supplied (the firm's details) for
        another value of the same kind -- one office's for another's. A fact a
        reviewer decided, or that any other source supports, is left alone.
        Returns whether it was replaced."""
        fact = self._facts.get(fact_key)
        if fact is not None and (fact.review is not None or fact.derived_by is not None
                                 or any(s.doc_type != doc_type for s in fact.sources)):
            return False
        self._facts.pop(fact_key, None)
        self.add_source(fact_key, doc_id, doc_type, value, value, 1.0)
        return True

    def set_aside(self, doc_ids: set[str], prefix: str = "") -> list[str]:
        """Takes the given documents' sources out of the facts under `prefix` (a document a person marked as someone else's: what it
        says is not the client's). A fact left with no source, no rule behind it and no reviewer's decision is dropped, so its box is
        empty; one that other sources still support is recomputed from them (a conflict the document caused is gone). Works on this
        copy only: the saved graph keeps every source, so setting the document back to the client restores them. Returns the fact keys
        that changed."""
        changed = []
        for key in [k for k in self._facts if k.startswith(prefix)]:
            fact = self._facts[key]
            kept = [s for s in fact.sources if s.doc_id not in doc_ids]
            if len(kept) == len(fact.sources):
                continue
            changed.append(key)
            fact.sources = kept
            if kept:
                self._recompute(fact)
                if fact.review is None and fact.resolution is not None and fact.status == "resolved" and fact.derived_by is None:
                    fact.resolution = None  # the disagreement it settled is gone
                if fact.derived_by is None and all(s.doc_type in TYPED_BY_A_PERSON for s in kept):
                    fact.tier = 3  # what is left is only what someone typed: a document no longer backs it, so a person checks it again
            elif fact.derived_by is None and fact.review is None:
                del self._facts[key]
        return changed

    def by_tier(self, tier: int) -> list[Fact]:
        return [f for f in self._facts.values() if f.tier == tier]

    def conflicts(self) -> list[Fact]:
        return [f for f in self._facts.values() if f.status == "conflict"]

    def missing(self, required_keys: list[str]) -> list[str]:
        """Required fact keys with no resolved value -- either never
        touched at all, or explicitly marked missing."""
        result = []
        for key in required_keys:
            fact = self._facts.get(key)
            if fact is None or fact.status == "missing":
                result.append(key)
        return result

    def to_dict(self) -> dict:
        facts_out = {}
        for key, fact in self._facts.items():
            d = asdict(fact)
            facts_out[key] = d
        return {"client_id": self.client_id, "facts": facts_out}

    @classmethod
    def from_dict(cls, data: dict) -> "FactGraph":
        graph = cls(data["client_id"])
        for key, fd in data["facts"].items():
            sources = [Source(**s) for s in fd.get("sources", [])]
            resolution = Resolution(**fd["resolution"]) if fd.get("resolution") else None
            fact = Fact(
                fact_key=fd["fact_key"],
                tier=fd["tier"],
                status=fd["status"],
                value=fd.get("value"),
                sources=sources,
                derived_by=fd.get("derived_by"),
                derived_from=fd.get("derived_from", []),
                resolution=resolution,
                missing_reason=fd.get("missing_reason"),
                review=Resolution(**fd["review"]) if fd.get("review") else None,
            )
            graph._facts[key] = fact
        return graph

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "FactGraph":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)
