"""Fictional name correction overlays preserve submitted and access evidence."""
# ruff: noqa: F811
import copy
import json

from fastapi.testclient import TestClient
from portal.app import create_app
import documents
import subject_attribution
from review.overview import name_and_kind
from test_prospects import firm, server, call  # noqa: F401
from test_staff_consent_http import client
from test_staff_questionnaire_access import access
from test_questionnaire_pdf import pdf_text
from portal.demo import answers


def test_corrected_applicant_name_matches_authorized_client_staff_exports_without_changing_submission(server, firm):
    cid = client(server, name="Fictional Original Applicant")
    case = firm["clients"] / cid
    token = access(server, cid)["url"].rsplit("/", 1)[1]
    with TestClient(create_app(firm["portal"])) as phone:
        assert phone.post("/api/access-link", json={"token": token}, headers={"X-Portal": "1"}).status_code == 200
        assert phone.put("/api/answers", json=answers(), headers={"X-Portal": "1"}).status_code == 200
        assert phone.post("/api/submit", json={"agree": True, "signature": "Fictional Original Applicant"}, headers={"X-Portal": "1"}).status_code == 200
        before = copy.deepcopy(firm["store"].profile(cid))
        submitted = copy.deepcopy(firm["store"].answers(cid))
        access_proof = (firm["portal"] / "clients" / cid / "communication_consent.json").read_bytes()
        data = documents.read(case) or {"version": 1, "documents": []}
        subject_attribution.ensure_catalog(data, cid)
        documents.save(case, data)
        person = next(p for p in data["case_subjects"]["people"] if p["case_role"] == "applicant")
        # A separately saved explicit review is deliberately newer than profile
        # and overview caches. Both authorized exports use the same overlay.
        subject_attribution.rename_person(case, person["id"], "Corrected Fictional Applicant", "Fictional Reviewer", "paralegal")
        (case / "overview.json").write_text(json.dumps({"row": {"summary": {"name": "Fictional Original Applicant"}}, "built": "2026-10-05T10:30:00+00:00"}))
        assert name_and_kind(case)[0] == "Corrected Fictional Applicant"
        assert phone.get("/api/me").json()["first_name"] == "Corrected"
        own = phone.get("/api/questionnaire.pdf")
        code, staff = call(server, "jane", "/api/questionnaire.pdf?client=" + cid)
        assert own.status_code == code == 200
        assert pdf_text(own.content) == pdf_text(staff)
        assert "Corrected Fictional Applicant" in pdf_text(staff)
        assert "Fictional Original Applicant" in pdf_text(staff)  # immutable signature
        assert firm["store"].profile(cid) == before
        assert firm["store"].answers(cid) == submitted
        assert (firm["portal"] / "clients" / cid / "communication_consent.json").read_bytes() == access_proof
