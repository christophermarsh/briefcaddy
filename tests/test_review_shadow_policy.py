import hashlib
import pytest
from review_automation import boundary_proposal, subject_proposal, evaluate
from extract.birth_certificate import extract


@pytest.mark.parametrize("heading", ["WITNESS", "TESTEMUNHA", "DECLARANT", "TRANSLATOR"])
def test_parent_block_ends_before_other_individual_roles(heading):
    fields = extract("REPUBLICA FEDERATIVA DO BRASIL\nFILIACAO\n" + heading + "\nANA FICTIONAL SILVA\nPAULO EXAMPLE REIS")
    assert not any("birth_cert.parent_" in field.fact_key for field in fields)


def test_boundary_shadow_preserves_original_ranges_and_human_source_version():
    plan = {"source_sha256": "a" * 64, "fingerprint": "b" * 64, "held": True,
            "instances": [{"first": 0, "last": 2, "type": "passport", "reasons": ["Reverse page uncertain"]}]}
    proposal = boundary_proposal(plan)
    assert proposal["proposed_starts"] == [0] and proposal["evidence"][0]["pages"] == [0, 2]
    assert not proposal["automatic_acceptance"]
    assert proposal["proposal_id"] != boundary_proposal(dict(plan, source_sha256="c" * 64))["proposal_id"]


def test_subject_shadow_requires_independent_identifier_and_abstains_shared_names():
    row = {"slots": ["holder"], "bound": True, "source_sha256": "a" * 64, "fingerprint": "f" * 64,
           "facts": [{"role": "holder", "key": "applicant.name", "value": "ANA FICTIONAL"},
                     {"role": "holder", "key": "applicant.passport_number", "value": "P12345"}]}
    person = {"id": "p1", "label": "ANA FICTIONAL", "identifiers": {"passport_number": "P12345"},
              "identifier_provenance": {"passport_number": {"basis": "independent", "by": "Reviewer", "history": ["reviewed"], "source_sha256": "b" * 64}}}
    assert subject_proposal(row, [person])["roles"]["holder"]["subject_id"] is None
    reviewed = {"bound": True, "current": True, "source_sha256": "b" * 64,
                "assignment": {"who": "Reviewer", "role": "paralegal", "roles": {"holder": {"subject_id": "p1"}}},
                "facts": [{"key": "applicant.passport_number", "role": "holder", "value": "P12345", "state": "accepted", "evidence_version": "v1"}]}
    assert subject_proposal(row, [person], reviewed_sources=[reviewed])["roles"]["holder"]["subject_id"] == "p1"
    assert subject_proposal(row, [person, dict(person, id="p2")], reviewed_sources=[reviewed])["roles"]["holder"]["subject_id"] is None
    for changed in (dict(reviewed, current=False), dict(reviewed, source_sha256=row["source_sha256"]), dict(reviewed, bound=False)):
        assert subject_proposal(row, [person], reviewed_sources=[changed])["roles"]["holder"]["subject_id"] is None
    reviewed["facts"][0]["value"] = "P98765"
    assert subject_proposal(row, [person], reviewed_sources=[reviewed])["roles"]["holder"]["subject_id"] is None


def test_shadow_evaluation_error_denominators_and_no_automatic_acceptance():
    rows = [{"case_id": "c1", "family_group": "f1", "source_sha256": hashlib.sha256(b"source").hexdigest(),
             "partition": "held_out", "corpus_digest": "a" * 64,
             "adjudication": {"basis": "independent", "by": "Reviewer", "history": ["reviewed"]},
             "true_starts": [0, 2], "proposed_starts": [0, 1], "true_person": "p1", "proposed_person": "p2"}]
    result = evaluate(rows)
    assert result["counts"]["false_splits"] == result["counts"]["false_merges"] == result["counts"]["wrong_person"] == 1
    assert result["counts"]["cases"] == result["counts"]["families"] == result["counts"]["sources"] == 1
    assert result["person_proposal_coverage"] == {"numerator": 1, "denominator": 1}
    assert not result["measurement_gate_met"] and not result["automatic_acceptance"]


def test_real_retained_i94_views_suggest_only_from_other_current_reviewed_source(tmp_path):
    import documents
    import subject_attribution as subjects
    from test_subject_attribution import saved, I94, client_id, confirm
    case = saved(tmp_path, [("first.pdf", I94[0]), ("second.pdf", I94[0] + "\nFICTIONAL SECOND ORIGINAL")])
    person_id = client_id(case)
    subjects.rename_person(case, person_id, "ALPHA EXAMPLE", "Fictional Reviewer", "paralegal")
    rows = subjects.views(case)
    assert len(rows) == 2 and all(r["bound"] for r in rows)
    confirm(case, rows[0], {"holder": person_id})
    rows = subjects.views(case)
    target = next(r for r in rows if not r["current"])
    people = documents.read(case)["case_subjects"]["people"]
    proposal = subject_proposal(target, people, reviewed_sources=rows)
    assert proposal["roles"]["holder"]["subject_id"] == person_id
    assert not proposal["automatic_acceptance"]
    other = next(r for r in rows if r["current"])
    (tmp_path / "source" / other["file"]).write_bytes(b"changed fictional original")
    rows = subjects.views(case)
    target = next(r for r in rows if r["instance_id"] == target["instance_id"])
    assert subject_proposal(target, people, reviewed_sources=rows)["roles"]["holder"]["subject_id"] is None


def test_existing_corpus_adapter_scores_independently_bound_source(tmp_path):
    from evaluation import corpus
    from evaluation.review_shadow import score
    source = tmp_path / "source.txt"
    source.write_text("Fictional passport\npage one")
    adj = {"basis": "independent", "by": "Fictional reviewer", "history": [{"reason": "Independent original inspection"}]}
    doc = {"id": "d1", "path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "language": "en", "quality": "clear",
           "type": "passport", "page_count": 1, "subject": "p1", "subject_scope": "validated_single_subject", "subject_adjudication": adj,
           "boundary_adjudication": adj, "instances": [{"id": "a" * 64, "pages": [0, 0]}]}
    manifest = {"version": 1, "id": "fictional-shadow", "synthetic_only": True, "cases": [
        {"id": "c1", "family_group": "f1", "partition": "evaluation", "subjects": ["p1"], "completion": "accepted", "documents": [doc], "expected": []}]}
    observation = {"policy": "review-shadow-1", "corpus_digest": corpus.digest(manifest), "documents": [
        {"case": "c1", "document": "d1", "source_sha256": doc["sha256"], "starts": [0], "subject": "p1"}]}
    result = score(manifest, tmp_path, observation)
    assert result["counts"]["person_proposals"] == result["counts"]["boundary_cases"] == 1
    assert result["counts"]["wrong_person"] == 0 and not result["automatic_acceptance"]
    observation["documents"].append(dict(observation["documents"][0]))
    with pytest.raises(ValueError, match="duplicated"):
        score(manifest, tmp_path, observation)
