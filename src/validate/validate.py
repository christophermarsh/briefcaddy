# Fictional OCR example: RAFAELA DEMONSTRA / RAFAELA DEMONSTRB differ by one glyph.
"""Application helper with evidence-bound inputs."""

from __future__ import annotations

from dataclasses import dataclass

from factgraph import FactGraph

LEVEL_ORDER = {"blocking": 0, "review": 1, "informational": 2}


@dataclass
class Flag:
    level: str  # Generic implementation note.
    fact_key: str
    message: str
    # Generic implementation note.
    # Generic implementation note.
    # Generic implementation note.
    # Generic implementation note.
    # Generic implementation note.
    kind: str = "fact"


# Generic implementation note.
# Generic implementation note.
# Generic implementation note.
EVIDENCE_ONLY = ("applicant.birth_cert.", "marriage.", "folder.")  # Generic implementation note.
# Generic implementation note.
# Generic implementation note.
CLIENT_CONTEXT = ("applicant.name_current_typed", "applicant.name_birth_typed")
# Generic implementation note.
# Generic implementation note.
NAMES_CARD_OPEN = "applicant.name_after_marriage_open"
ASKED_ON_NAMES_CARD = ("applicant.given_name", "applicant.family_name")


def _traceable(fact) -> bool:
    if fact.resolution is not None or fact.review is not None:
        return True
    return any(fact.value == source.normalized_value for source in fact.sources)


def validate_graph(graph: FactGraph, required_fact_keys: list[str]) -> list[Flag]:
    """Application helper with evidence-bound inputs."""
    # Generic implementation note.
    # Generic implementation note.
    flags: list[Flag] = __import__('critical_review').flags(graph)
    facts = graph.all_facts()

    for key in required_fact_keys:
        fact = facts.get(key)
        if fact is None:
            flags.append(
                Flag(
                    "blocking",
                    key,
                    f"{key}: not attempted. No extractor or rule produced this fact. "
                    "Needs investigation before this case can be filed.",
                )
            )
        elif fact.status == "missing":
            flags.append(
                Flag(
                    "blocking",
                    key,
                    f"{key}: no source found ({fact.missing_reason}). "
                    "Needs client input before this case can be filed.",
                )
            )

    for key, fact in facts.items():
        if fact.status == "conflict" and key.startswith(EVIDENCE_ONLY):
            continue  # Generic implementation note.
        if fact.status == "conflict":
            # Generic implementation note.
            # Generic implementation note.
            # Generic implementation note.
            # Generic implementation note.
            values = sorted({str(s.normalized_value) for s in fact.sources})
            detail = "; ".join(f"{s.doc_id}: {s.normalized_value!r}" for s in fact.sources)
            flags.append(
                Flag(
                    "review",
                    key,
                    f"{key}: sources disagree ({', '.join(values)}): field LEFT BLANK until resolved. {detail}",
                )
            )
            continue

        if fact.status != "resolved":
            continue

        if fact.review is not None:
            pass  # Generic implementation note.
        elif key.startswith("questionnaire.") or key in CLIENT_CONTEXT:
            pass  # Generic implementation note.
        elif key in ASKED_ON_NAMES_CARD and NAMES_CARD_OPEN in facts:
            pass  # Generic implementation note.
        elif fact.tier == 2:
            flags.append(
                Flag(
                    "review",
                    key,
                    f"{key}: derived by rule {fact.derived_by}: needs a human sign-off.",
                )
            )
        elif fact.tier == 3:
            flags.append(
                Flag(
                    "review",
                    key,
                    f"{key}: Tier 3 (human-supplied or confirmed, not from a document). Needs sign-off.",
                )
            )

        if fact.sources and not _traceable(fact):
            flags.append(
                Flag(
                    "blocking",
                    key,
                    f"{key}: resolved value {fact.value!r} matches none of its recorded sources and has no "
                    "resolution record: traceability broken, needs investigation.",
                )
            )

    flags.sort(key=lambda f: LEVEL_ORDER[f.level])
    return flags
