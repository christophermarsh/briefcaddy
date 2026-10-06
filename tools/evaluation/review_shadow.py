"""Score source-bound shadow proposals against independently adjudicated corpus references."""
from . import corpus
from review_automation import evaluate


def score(manifest, root, observations):
    corpus.validate(manifest, root)
    digest = corpus.digest(manifest)
    if observations.get("corpus_digest") != digest or observations.get("policy") != "review-shadow-1":
        raise ValueError("Shadow observations must bind this exact corpus and policy.")
    lookup, seen = {}, set()
    for case in manifest["cases"]:
        for doc in case["documents"]:
            lookup[(case["id"], doc["id"])] = case, doc
    rows = []
    for observation in observations.get("documents", []):
        key = observation.get("case"), observation.get("document")
        if key not in lookup or key in seen:
            raise ValueError("Unknown or duplicated shadow source observation.")
        seen.add(key)
        case, doc = lookup[key]
        if observation.get("source_sha256") != doc["sha256"]:
            raise ValueError("Shadow observation source differs from reference bytes.")
    # Missing observations still count as abstentions. Calibration cannot turn
    # into a held-out claim, and workflow labels never acquire adjudication here.
    observed = {(row["case"], row["document"]): row for row in observations.get("documents", [])}
    for key, (case, doc) in lookup.items():
        if case["partition"] != "evaluation":
            continue
        observation = observed.get(key, {})
        adjudication = doc.get("subject_adjudication") or {}
        subject_valid = doc.get("subject_scope") == "validated_single_subject" and adjudication.get("basis") == "independent"
        if adjudication.get("basis") == "independent":
            corpus.adjudication(adjudication)
        row = {"case_id": case["id"], "family_group": case["family_group"], "source_sha256": doc["sha256"],
               "partition": "held_out", "corpus_digest": digest, "adjudication": adjudication,
               "proposed_person": observation.get("subject"), "unadjudicated_dimensions": []}
        if subject_valid:
            row["true_person"] = doc.get("subject")
        else:
            row["unadjudicated_dimensions"].append("person")
        boundary = doc.get("boundary_adjudication") or {}
        if doc.get("instances") and boundary.get("basis") == "independent":
            corpus.adjudication(boundary)
            if adjudication.get("basis") != "independent":
                row["adjudication"] = boundary
            row.update(true_starts=[instance["pages"][0] for instance in doc["instances"]],
                       proposed_starts=observation.get("starts", []))
        else:
            row["unadjudicated_dimensions"].append("boundary")
        rows.append(row)
    return dict(evaluate(rows), corpus_digest=digest)
