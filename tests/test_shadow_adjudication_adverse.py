"""Fictional boundary adjudication cannot stand in for person adjudication."""

import hashlib

from evaluation import corpus
from evaluation.review_shadow import score


def test_boundary_review_cannot_validate_unknown_person_assignment(tmp_path):
    source = tmp_path / "fictional.txt"
    source.write_text("Fictional original, one page")
    independent = {
        "basis": "independent",
        "by": "Fictional Boundary Reviewer",
        "history": [{"reason": "Reviewed page boundary only; person remains unresolved"}],
    }
    document = {
        "id": "doc-one", "path": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "language": "en", "quality": "clear", "type": "passport", "page_count": 1,
        "subject": "person-one", "subject_scope": "unknown",
        "boundary_adjudication": independent,
        "instances": [{"id": "a" * 64, "pages": [0, 0]}],
    }
    manifest = {
        "version": 1, "id": "fictional-boundary-only", "synthetic_only": True,
        "cases": [{
            "id": "case-one", "family_group": "family-one", "partition": "evaluation",
            "subjects": ["person-one"], "completion": "held", "documents": [document], "expected": [],
        }],
    }
    observations = {
        "policy": "review-shadow-1", "corpus_digest": corpus.digest(manifest),
        "documents": [{
            "case": "case-one", "document": "doc-one", "source_sha256": document["sha256"],
            "starts": [0], "subject": "person-one",
        }],
    }
    result = score(manifest, tmp_path, observations)
    assert result["counts"]["person_cases"] == 0
    assert result["counts"]["person_proposals"] == 0
    assert not result["measurement_gate_met"]
    assert not result["automatic_acceptance"]
