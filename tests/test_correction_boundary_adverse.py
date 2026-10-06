"""Historical reference observations cannot borrow a newly staged boundary."""
# ruff: noqa: F811 -- shared canonical fictional fixture
import json
from types import SimpleNamespace

from test_correction_provenance import authorize, observation  # noqa: F401
from test_reader_examples import clients, make_case  # noqa: F401

import documents
import evaluation_candidates
import purge
import reader_examples
from communication_fixture import STAFF
from document_instances import digest
from factgraph import FactGraph


def test_reference_comparison_does_not_backfill_new_boundary_into_old_read(clients):
    case = make_case(clients)
    graph = FactGraph.load(case / "fact_graph.json")
    key = "applicant.date_of_birth"
    old_source = graph.get(key).sources[0]
    data = documents.read(case)
    plan = data["boundary_plans"]["passport-ana.pdf"]
    part = plan["instances"][0]
    old_instance = part["instance_id"]
    old_boundary = part["evidence_fingerprint"]
    # A later OCR/boundary pass can retain the original bytes and page range
    # (hence instance ID) while its text/segmenter evidence changes.
    part["evidence_fingerprint"] = digest({"fictional": "new boundary reading"})
    plan["fingerprint"] = digest({"fictional": "new boundary plan"})
    documents.save(case, data)
    assert part["instance_id"] == old_instance
    assert part["evidence_fingerprint"] != old_boundary

    comparison = {
        "form": "fictional-reference",
        "boxes": [{"key": key, "field": "fictional-dob", "kind": "different",
                   "cause": "reader", "ours": "2006-03-14", "reference": "2006-03-15"}],
    }
    paths = reader_examples.from_reference(
        clients, SimpleNamespace(case=case.name, graph=graph), comparison
    )
    assert len(paths) == 1
    record = json.loads(paths[0].read_text(encoding="utf-8"))
    proof = record["source_provenance"]
    assert proof["source_version"] == old_source.evidence_version
    assert proof["state"] != "verified"
    assert proof["boundary_fingerprint"] != part["evidence_fingerprint"]
    assert record["observation"]["adjudication"] == "pending"


def test_approved_candidate_export_is_held_while_purge_is_waiting(observation, monkeypatch):
    home, _case, _stored, _path, record = observation
    root, grant, out = authorize(home, record)
    actual_entry = purge.entry

    def waiting_entry(clients_root, case):
        if case == record["case"]:
            return {"case": case, "state": "waiting"}
        return actual_entry(clients_root, case)

    monkeypatch.setattr(purge, "entry", waiting_entry)
    try:
        evaluation_candidates.export_candidates(home, root, grant, STAFF, [record["case"]], out)
    except (ValueError, PermissionError):
        pass
    assert not out.exists(), "A retained approval must not override the current purge lifecycle hold."
