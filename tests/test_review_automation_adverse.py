"""Independent fictional probes for shadow-evaluation and identity claims."""
import hashlib


def _rows(*, same_case=False, abstain=False):
    return [{"case_id": "fictional-case" if same_case else f"fictional-case-{i}",
             "family_group": "fictional-family" if same_case else f"fictional-family-{i}",
             "source_sha256": hashlib.sha256(str(i).encode()).hexdigest(),
             "partition": "held_out", "adjudicated": True,
             "corpus_digest": "a" * 64,
             "adjudication": {"basis": "independent", "by": "Fictional Independent Reviewer",
                              "history": [{"reason": "Independent fictional source inspection"}]},
             "true_starts": [0], "proposed_starts": [0],
             "true_person": "fictional-person", "proposed_person": None if abstain else "fictional-person"}
            for i in range(100)]


def _assert_no_gate(rows):
    from review_automation import evaluate
    try:
        result = evaluate(rows)
    except ValueError:
        return  # Invalid evaluation evidence may instead be rejected explicitly.
    assert result["measurement_gate_met"] is False
    assert result["automatic_acceptance"] is False


def test_abstaining_on_every_person_cannot_satisfy_measurement_gate():
    _assert_no_gate(_rows(abstain=True))


def test_one_case_with_many_sources_cannot_satisfy_independent_case_minimum():
    _assert_no_gate(_rows(same_case=True))


def test_matching_person_identifier_without_independent_provenance_abstains():
    from review_automation import subject_proposal
    source_hash = "a" * 64
    row = {"slots": ["applicant"], "bound": True, "source_sha256": source_hash,
           "fingerprint": "b" * 64, "facts": [
               {"role": "applicant", "key": "applicant.name", "value": "ANA FICTIONAL"},
               {"role": "applicant", "key": "applicant.passport_number", "value": "P123456"}]}
    people = [{"id": "fictional-person", "label": "ANA FICTIONAL", "active": True,
               "identifiers": {"passport_number": "P123456"}}]
    proposal = subject_proposal(row, people)
    assert proposal["roles"]["applicant"]["subject_id"] is None
    assert proposal["automatic_acceptance"] is False
