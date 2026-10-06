"""One fresh fictional case through current protected HTTP and real workers."""
import base64
import json

from fastapi.testclient import TestClient

import document_instances
import documents
import jobs
import critical_review
from portal import app as portal_app, communication_consent as consent
from review.state import reviewed_graph, load_decision_log
from test_client_communication_consent import choice
from cloud_daily_work_fixtures import (
    cloud_world, cloud_app, cloud_server, ATTORNEY, STAFF, OTHER_STAFF, PASSWORD,
    NAME, EMAIL, PHONE, I94, NTA, MARRIAGE, PORTAL_ANSWERS, REMOTE, pdf, request, ok,
    login, review_wording, last_token, work, install_selected_drive,
    review_source_subjects, field_card, decide_field,
)

# Fixture imports are intentional pytest injection.
# ruff: noqa: F811, F401


def test_one_current_case_daily_workflow(cloud_world, cloud_app, cloud_server, monkeypatch):
    world, base = cloud_world, cloud_server
    scope, store = world["scope"], world["store"]
    assert not list(scope.cases.iterdir())
    attorney = login(base, ATTORNEY)
    staff = login(base, STAFF)
    # A real new account, one-time credential and password change over HTTP.
    assert request(base, "/api/staff", staff, {"action": "add", "email": OTHER_STAFF,
        "name": "Fictional Transfer Staff", "role": "paralegal"})[0] == 403
    added = ok(base, "/api/staff", attorney, {"action": "add", "email": OTHER_STAFF,
        "name": "Fictional Transfer Staff", "role": "paralegal"})
    status, headers, raw = request(base, "/api/password", body={"email": OTHER_STAFF,
        "password": added["one_time_password"], "new_password": PASSWORD})
    assert status == 200, raw
    transfer = headers["Set-Cookie"].split(";", 1)[0]
    conflict = ok(base, "/api/conflict-search", staff, {"purpose": "add", "name": NAME})
    created = ok(base, "/api/client-add", staff, {"name": NAME, "phone": PHONE,
        "email": EMAIL, "language": "en", "filing": "i485", "invite": False,
        "conflict": {"search": conflict["id"], "decision": "none"}})
    cid = created["id"]
    world["client"] = cid
    case = scope.cases / cid
    profile = store.profile(cid)
    assert profile["name"] == NAME and profile["phone"] == PHONE and profile["email"] == EMAIL
    assert profile["filing"] == "i485" and not profile.get("invited_at")
    assert not (case / "fact_graph.json").exists() and not world["sent"]
    assert not ok(base, "/api/communication?client=" + cid, staff)["can_send"]
    held = ok(base, "/api/client-invite", staff, {"client": cid})
    assert held["delivery"]["status"] == "none" and not world["sent"]
    review_wording(base, attorney)
    # Manual office assistance gives access to signoff only, never verification.
    manual = ok(base, "/api/client-link", attorney, {"client": cid})
    with TestClient(portal_app.create_app(scope.portal, base_url="http://testserver",
                                        secure_cookies=False)) as client:
        response = client.get(manual["url"].replace("http://testserver", ""), follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/consent"
        assert client.get("/api/me").status_code == 401
        context = client.get("/api/communication/consent").json()
        body = choice(context) | {"typed_name": NAME}
        signed = client.post("/api/communication/consent", json=body, headers={"X-Portal": "1"})
        assert signed.status_code == 200 and signed.json()["state"] == "granted"
        assert not world["sent"] and client.get("/api/me").status_code == 401
        grant = consent._record(store.communication_scope(), store, cid)["channels"]["email"]
        assert grant["method"] == "client_portal" and grant["actor"] == "client:" + cid
        invited = ok(base, "/api/client-invite", staff, {"client": cid})
        assert invited["delivery"]["status"] == "sent" and len(world["sent"]) == 1
        assert not ok(base, "/api/communication?client=" + cid, staff)["email_control"]
        response = client.get("/l/" + last_token(world), follow_redirects=False)
        assert response.status_code == 303 and client.get("/api/me").status_code == 200
        answers = client.put("/api/answers", json=PORTAL_ANSWERS, headers={"X-Portal": "1"})
        assert answers.status_code == 200 and not answers.json()["errors"], answers.text
        assert set(PORTAL_ANSWERS) <= set(answers.json()["accepted"])
        content = pdf(I94)
        uploaded = client.post("/api/upload", data={"doc_id": "i94", "attempt": "1" * 32},
            files={"file": ("Fictional I94.pdf", content, "application/pdf")}, headers={"X-Portal": "1"})
        assert uploaded.status_code == 200, uploaded.text
        outcome = uploaded.json()["upload_outcome"]
        assert outcome["received"] and outcome["reading"]
        stored = store.uploads(cid)[0]
        assert (store.client_dir(cid) / "uploads" / stored["stored"]).read_bytes() == content
        # Actual canonical setup then installed registered portal handlers.
        associated = ok(base, "/api/source-setup", staff, {"client": cid})
        assert associated["associated"]
        assert work(world) >= 1
        assert (case / "fact_graph_raw.json").is_file()
        assert (scope.documents / cid / "source" / stored["stored"]).read_bytes() == content
        # A separate durable office upload, retained receipt and real staff handler.
        office = ok(base, "/api/client-upload", staff, {"client": cid, "name": "Fictional NTA.pdf",
            "data": base64.b64encode(pdf(NTA)).decode(), "attempt": "2" * 32})
        assert office["received"] and not office["processed"]
        assert work(world) >= 1
        job = jobs.get(scope.queue, office["job"]["id"])
        assert job["kind"] == "staff_upload" and job["state"] == "done", job
        assert (scope.documents / cid / "source" / office["name"]).read_bytes() == pdf(NTA)
        provider = install_selected_drive(world, monkeypatch)
        configured = ok(base, "/api/drive-settings", attorney, {"revision": 0, "attempt": "3" * 32,
            "provider": {"root_folder_id": "fictional-daily-root", "documents_subfolder": None},
            "bindings": {REMOTE: cid}})
        assert configured["revision"] == 1
        preview = ok(base, "/api/drive-preview", staff, {"client": cid, "selected": [REMOTE]})
        assert provider["downloads"] == 0 and "fictional-unselected-private" not in json.dumps(preview)
        queued = ok(base, "/api/drive-enqueue", staff, {"client": cid, "preview": preview, "attempt": "4" * 32})
        assert not queued["completed"] and work(world) >= 1
        drive_job = jobs.get(scope.queue, queued["jobs"][REMOTE]["id"])
        assert drive_job["state"] == "done", drive_job
        assert provider["downloads"] == 1
        marriage = ok(base, "/api/client-upload", staff, {"client": cid, "name": "Fictional Marriage.pdf",
            "data": base64.b64encode(pdf(MARRIAGE)).decode(), "attempt": "7" * 32})
        assert marriage["received"] and not marriage["processed"] and work(world) >= 1
        assert jobs.get(scope.queue, marriage["job"]["id"])["state"] == "done"
        rows = documents.read(case)["documents"]
        assert {"portal", "drive"} <= {row["source"] for row in rows}
        assert all(not row["stale"] and not row.get("processing_incomplete") for row in document_instances.views(case))
        assert reviewed_graph(case).get("applicant.p14_block1_text") is not None
        # Explicit review opens current retained pages then maps these known
        # fictional documents to case people. Reading alone did not do this.
        evidence = review_source_subjects(base, staff, cid)
        assert all(row["current"] for row in evidence["subject_reviews"])
        comparison = field_card(base, staff, cid, "applicant.marriage_cert_birthplace")
        assert comparison["comparison"]["kind"] == "specificity"
        assert comparison["comparison"]["filed_city"]["value"] == "CAMPINAS"
        key = "applicant.i94_number"
        current = critical_review.context(case)
        assert key in current and current[key]["bound"]
        decide_field(base, staff, cid, key, "11111111111",
            note="FICTIONAL manual comparison of the retained I94 number on its own current original page.")
        assert reviewed_graph(case).get(key).value == "11111111111"
        multiline = "Fictional Example Shop work.\nA second retained line in this continuation."
        decide_field(base, staff, cid, "applicant.p14_block1_text", multiline,
                     note="FICTIONAL office corrected this continuation from the client's job history.")
        assert "\n" in reviewed_graph(case).get("applicant.p14_block1_text").value
        plan = ok(base, "/api/packet?client=" + cid + "&filing=i485", staff)
        assert not plan["ready"]
        items = ok(base, "/api/items?client=" + cid, staff)
        legal = next(item for item in items["open"]
                     if item["group"] == "attorney" and "acknowledge" in item["actions"])
        signoff = {"client": cid, "item_id": legal["id"], "action": "acknowledge",
            "reviewer": "Spoofed Attorney", "role": "attorney",
            "note": "FICTIONAL engineering review of this current alert; remaining packet holds stay unresolved; no live filing."}
        assert request(base, "/api/decide", staff, signoff)[0] == 403
        ok(base, "/api/decide", attorney, signoff)
        decision = load_decision_log(case)[legal["id"]]
        assert decision["role"] == "attorney" and decision["reviewer"] == "Fictional Attorney"
        assert not ok(base, "/api/packet?client=" + cid + "&filing=i485", staff)["ready"]
        # Current unsaved agreement preview never sends or signs the letter.
        before = len(world["sent"])
        preview = ok(base, "/api/engagement-preview", staff, {"client": cid, "filings": ["i485"],
            "fee": "Fictional professional services: 100 test units; payable after review.",
            "government_fees": "", "additions": "Fictional unsaved preview only."})
        assert preview["preview_sha256"] and len(world["sent"]) == before
        assert not (case / "engagement.json").exists()
        # Current claim/transfer retains actual actor and history on this same case.
        assigned = ok(base, "/api/assignment", staff, {"client": cid, "action": "claim",
            "revision": 0, "operation_id": "5" * 32})
        detail = ok(base, "/api/assignment?client=" + cid, staff)
        assert assigned["revision"] == detail["revision"] == 1
        target = next(person for person in detail["eligible_assignees"] if person["name"] == "Fictional Transfer Staff")
        moved = ok(base, "/api/assignment", transfer, {"client": cid, "action": "reassign",
            "revision": 1, "operation_id": "6" * 32, "assignee": target["id"], "reason": "Fictional daily workload handoff"})
        final = ok(base, "/api/assignment?client=" + cid, transfer)
        assert moved["revision"] == final["revision"] == 2 and len(final["history"]) == 2
        assert final["history"][0]["actor"]["name"] == "Fictional Staff"
        assert final["history"][1]["actor"]["name"] == "Fictional Transfer Staff"
        assert all(row["audit"]["state"] == "recorded" for row in final["history"])
