"""EV2 real ingestion and form mapping using retained fictional PDFs."""

import documents
import subject_attribution as subjects
from factgraph import FactGraph
from review.state import reviewed_graph
from test_subject_attribution import I94, saved, client_id, confirm


def test_petitioner_proof_fills_only_petitioner_fields_and_keeps_exhibit(tmp_path):
    from test_family import US_PASSPORT
    from fill.companion import fill_companions, load_profile
    from pypdf import PdfReader
    case = saved(tmp_path, [("petitioner.pdf", US_PASSPORT)])
    row = subjects.views(case)[0]
    raw = FactGraph.load(case / "fact_graph_raw.json")
    # This passport reader deliberately does not extract a person's name/DOB;
    # its MRZ name helper is a catalog suggestion, not an accepted form value.
    assert raw.get("petitioner.family_name") is None and raw.get("petitioner.status").value == "USC"
    assert row["held"] and reviewed_graph(case).get("petitioner.status") is None
    petitioner = subjects.add_person(case, "MICHAEL EXEMPLO", "petitioner", "Reviewer", "paralegal")
    confirm(case, row, {"holder": petitioner["id"]})
    graph = reviewed_graph(case)
    assert graph.get("petitioner.status").value == "USC" and graph.get("petitioner.us_passport").value == "Yes"
    assert graph.get("applicant.family_name") is None and not subjects.problems(case)
    profile = load_profile()
    profile["forms"] = {"i130": profile["forms"]["i130"]}
    fill_companions(graph, case, profile)
    out = case / "i130_filled.pdf"
    boxes = {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in PdfReader(out).get_fields().items()}
    assert boxes["Pt2Line36_USCitizen[0]"] == "/Y"
    assert boxes.get("Pt2Line4a_FamilyName[0]") in (None, "")
    assert boxes.get("Pt4Line4a_FamilyName[0]") in (None, "")
    assert documents.read(case)["documents"][0]["type"] == "us_passport"


def test_inbox_reread_preserves_unchanged_assignment_but_replacement_holds(tmp_path):
    from inbox import reprocess_documents
    from synthetic_documents import retain_documents
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    folder = tmp_path / "source"
    before = subjects.views(case)[0]
    confirm(case, before, {"holder": client_id(case)})
    reprocess_documents(case, folder, [("scan.pdf", I94[0])], {}, pages={"scan.pdf": [I94[0]]})
    assert subjects.views(case)[0]["current"] and reviewed_graph(case).get("applicant.given_name").value == "ALPHA"
    retain_documents(folder, [("scan.pdf", I94[1])])
    reprocess_documents(case, folder, [("scan.pdf", I94[1])], {}, pages={"scan.pdf": [I94[1]]})
    after = subjects.views(case)[0]
    assert after["instance_id"] != before["instance_id"] and after["held"]
    assert reviewed_graph(case).get("applicant.dob") is None
    assert before["instance_id"] in documents.read(case)["subject_assignments"]  # historical decision retained


def test_portal_ingestion_and_reprocess_require_current_subjects(tmp_path):
    from portal.store import PortalStore
    from portal.engine import process_client
    from portal.demo import document_pdf
    import packet
    store = PortalStore(tmp_path / "portal")
    store.add_client("synthetic", "Synthetic Client", email="synthetic@example.invalid", language="en")
    upload = store.add_upload("synthetic", "i94", "scan.pdf", document_pdf(I94[0].splitlines()), "application/pdf")
    root = tmp_path / "clients"
    process_client(store, "synthetic", root, use_policies=False)
    case = root / "synthetic"
    row = subjects.views(case)[0]
    assert row["bound"] and row["held"] and reviewed_graph(case).get("applicant.given_name") is None
    plan = packet.plan(case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0})
    assert not plan["ready"] and any("whose facts" in p for p in plan["problems"])
    assert row["source_sha256"] == upload["sha256"]
    confirm(case, row, {"holder": client_id(case)})
    process_client(store, "synthetic", root, use_policies=False)
    assert subjects.views(case)[0]["current"] and reviewed_graph(case).get("applicant.given_name").value == "ALPHA"


def test_subject_correction_rebuilds_client_questions_without_reviving_old_review(tmp_path):
    from portal.engine import sync_confirmations
    from portal.store import PortalStore
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    store = PortalStore(tmp_path / "portal")
    store.add_client(case.name, "Synthetic Client", email="synthetic@example.invalid", language="en")
    for name in ("fact_graph.json", "fact_graph_raw.json"):
        graph = FactGraph.load(case / name)
        graph.add_source("applicant.given_name", "portal questionnaire", "intake_questionnaire", "GAMMA", "GAMMA", .95, tier=3)
        graph.save(case / name)
    row = subjects.views(case)[0]
    assert sync_confirmations(store, case.name, case) == []
    confirm(case, row, {"holder": client_id(case)})
    assert any(t["question"] == "given_name" for t in sync_confirmations(store, case.name, case))
    relative = subjects.add_person(case, "ALPHA EXAMPLE", "child_1", "Reviewer", "paralegal")
    confirm(case, row, {"holder": relative["id"]})
    assert sync_confirmations(store, case.name, case) == []
    assert reviewed_graph(case).get("applicant.given_name").value == "GAMMA"
    assert len(documents.read(case)["documents"]) == 1
    confirm(case, row, {"holder": client_id(case)})
    assert any(t["question"] == "given_name" for t in sync_confirmations(store, case.name, case))


def test_correcting_case_person_name_reopens_bound_assignments(tmp_path):
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    row = subjects.views(case)[0]
    identity = client_id(case)
    confirm(case, row, {"holder": identity})
    person = subjects.rename_person(case, identity, "ALPHA EXAMPLE", "Reviewer", "paralegal")
    assert person["id"] == identity and person["revision"] == 2 and person["history"][0]["who"] == "Reviewer"
    assert subjects.views(case)[0]["held"] and reviewed_graph(case).get("applicant.given_name") is None
    confirm(case, row, {"holder": identity})
    assert reviewed_graph(case).get("applicant.given_name").value == "ALPHA"


def test_unconfirmed_notice_printed_dates_are_staff_attention_only(tmp_path):
    import pytest
    import journey
    from test_inbox import RFE, TODAY, confirm_notice_subjects, confirm_notice_sources
    case = saved(tmp_path, [("notice.pdf", "\n".join(RFE))])
    before = journey.journey(case, TODAY)
    pending = before["pending_evidence"]
    assert pending and pending[0]["file"] == "notice.pdf"
    assert any(d["label"] == "Printed response date" and d["value"] == "2026-12-28" for d in pending[0]["dates"])
    assert not before["deadlines"] and not before["notices"]
    assert not any(e["date"] == "2026-09-30" for e in journey.client_view(before, "en")["happened"])
    assert journey.summary(before)["urgent_steps"] > 0 and journey.summary(before)["deadlines"] == []
    with pytest.raises(ValueError, match="fact subjects"):
        journey.mark(case, "done", "Reviewer", "subject_notice:" + pending[0]["instance_id"])
    confirm_notice_subjects(case, "notice.pdf")
    # A subject assignment establishes whose notice it is; printed dates
    # still wait for the separate source-value check before becoming deadlines.
    assert journey.journey(case, TODAY)["pending_evidence"]
    confirm_notice_sources(case)
    after = journey.journey(case, TODAY)
    assert not after["pending_evidence"] and any(d["date"] == "2026-12-28" for d in after["deadlines"])
    assert any(e["date"] == "2026-09-30" for e in journey.client_view(after, "en")["happened"])
