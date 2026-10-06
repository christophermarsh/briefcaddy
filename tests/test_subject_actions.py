"""EV2 uses the existing authenticated, case-protected document action."""
import threading

import pytest

import restricted
import schema_path
import subject_attribution as subjects
from test_restricted import call, sign_in, PASSWORD
from test_subject_attribution import saved, I94, client_id


@pytest.fixture
def world(tmp_path):
    from review.auth import Accounts
    from review.server import ReviewApp, serve, make_handler
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    accounts = Accounts(tmp_path / "staff.json")
    email = "reviewer@firm.example"
    accounts.change_password(email, accounts.add(email, "Signed In Reviewer", "paralegal"), PASSWORD)
    app = ReviewApp(case.parent, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=accounts)
    httpd = serve(app, 0)
    httpd.RequestHandlerClass = make_handler(app, httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    cookie = sign_in(base, email)
    yield case, base, cookie, app
    httpd.shutdown()


def payload(case, **extra):
    row = subjects.views(case)[0]
    return {"client": case.name, "field": "subject_assignment", "id": row["instance_id"], "fingerprint": row["fingerprint"],
            "mappings": {"holder": client_id(case)}, "reviewer": "Forged Attorney", "role": "attorney", **extra}


def test_route_records_signed_in_actor_and_undo_reopens_hold(world):
    case, base, cookie, app = world
    status, text = call(base + "/api/document", cookie, payload(case))
    assert status == 200, text
    row = subjects.views(case)[0]
    assert row["current"] and row["assignment"]["who"] == "Signed In Reviewer" and row["assignment"]["role"] == "paralegal"
    status, text = call(base + f"/api/file?client={case.name}&doc=scan.pdf", cookie)
    assert status == 200 and "%PDF" in text
    status, text = call(base + "/api/document", cookie, payload(case, field="subject_undo", mappings={}))
    assert status == 200, text
    assert subjects.views(case)[0]["held"]


@pytest.mark.parametrize("change", [{"fingerprint": "stale"}, {"mappings": {"invented_role": "invented_person"}},
                                     {"mappings": []}, {"reference_edges": [None]}])
def test_bad_proof_or_unsupported_role_does_not_persist(world, change):
    case, base, cookie, app = world
    before = (case / "documents.json").read_bytes()
    status, text = call(base + "/api/document", cookie, payload(case, **change))
    assert status == 400, text
    assert (case / "documents.json").read_bytes() == before


def test_restricted_case_hides_review_original_and_rejects_mutation(world):
    case, base, cookie, app = world
    restricted.mark(case, True, "Synthetic protected case", "Attorney", "attorney")
    before = (case / "documents.json").read_bytes()
    for suffix in (f"/api/documents?client={case.name}", f"/api/file?client={case.name}&doc=scan.pdf"):
        status, text = call(base + suffix, cookie)
        assert status == 404 and "ALPHA" not in text and "scan.pdf" not in text
    status, text = call(base + "/api/document", cookie, payload(case))
    assert status == 404
    assert (case / "documents.json").read_bytes() == before


def test_unsupported_actor_role_is_rejected_before_persistence(world):
    case, base, cookie, app = world
    before = (case / "documents.json").read_bytes()
    with pytest.raises(ValueError, match="staff reviewer"):
        app.document_change(case.name, payload(case), "support")
    assert (case / "documents.json").read_bytes() == before
