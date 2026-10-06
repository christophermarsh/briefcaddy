"""Actual authenticated HTTP, fictional retained PDFs, no provider/service calls."""
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest

import jobs
import restricted
from portal.demo import document_pdf
from upload_workflow_fixtures import source_firm, world, app, server, call, sign_in  # noqa: F401 -- pytest fixture registration and helper reexports

ATTEMPT = "a" * 32


def body(world, **changes):  # noqa: F811 -- pytest fixture injection
    return {"client": world["client"], "name": "Fictional I94.pdf", "attempt": ATTEMPT,
            "data": base64.b64encode(document_pdf(world["pages"][0].splitlines())).decode(), **changes}


def outcome_url(server, world, attempt=ATTEMPT):  # noqa: F811 -- pytest fixture injection
    return server + "/api/staff-upload-outcome?client=" + quote(world["client"]) + "&attempt=" + attempt


def posted(server, cookie, world, **changes):  # noqa: F811 -- pytest fixture injection
    code, raw = call(server + "/api/client-upload", cookie, body(world, **changes))
    return code, json.loads(raw)


def no_upload_effects(world):  # noqa: F811 -- pytest fixture injection
    scope, client = world["scope"], world["client"]
    assert not (scope.cases / client / "staff-upload-receipts").exists()
    assert not list(scope.queue.glob("*-staff_upload-*.json"))
    assert not list(scope.queue.glob("operation-*.json"))
    assert world["store"].uploads(client) == []
    assert not list((world["store"].client_dir(client) / "uploads").glob("*.pdf"))


def test_same_attempt_one_file_row_job_and_observe_only(world, app, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    wakes = []
    monkeypatch.setattr(jobs, "ensure_worker", lambda *args: wakes.append(args) or False)
    code, first = posted(server, cookie, world, reviewer="Forged Attorney", role="attorney", documents_root="/foreign")
    assert code == 200, first
    assert first["received"] and first["reading"] and not first["processed"] and first["review_required"]
    assert first["worker_available"] is False and first["by"] == "Jane Fictional"
    code, again = posted(server, cookie, world)
    assert code == 200 and again["name"] == first["name"] and again["job"]["id"] == first["job"]["id"]
    count = len(wakes)
    code, raw = call(outcome_url(server, world), cookie)
    assert code == 200 and json.loads(raw)["job"]["id"] == first["job"]["id"] and len(wakes) == count
    scope, client = world["scope"], world["client"]
    assert len(world["store"].uploads(client)) == len(list(scope.queue.glob("*-staff_upload-*.json"))) == 1
    assert len(list((world["store"].client_dir(client) / "uploads").glob("*.pdf"))) == 1
    assert not (scope.cases / client / "fact_graph.json").exists()


def test_committed_upload_lost_response_reconciles_same_job(world, app, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    def lost(*_):
        raise OSError("synthetic response loss after durable upload")
    monkeypatch.setattr(app, "_wake_upload_worker", lost)
    code, _ = posted(server, cookie, world)
    assert code == 500
    code, raw = call(outcome_url(server, world), cookie)
    observed = json.loads(raw)
    assert code == 200 and observed["received"] and not observed["processed"]
    monkeypatch.setattr(app, "_wake_upload_worker", lambda *_: False)
    code, repaired = posted(server, cookie, world)
    assert code == 200 and repaired["job"]["id"] == observed["job"]["id"]
    assert len(world["store"].uploads(world["client"])) == 1
    assert len(list(world["scope"].queue.glob("*-staff_upload-*.json"))) == 1


@pytest.mark.parametrize("change", ["bytes", "name"], ids=["changed-bytes", "changed-name"])
def test_changed_attempt_refused(world, server, change):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    assert posted(server, cookie, world)[0] == 200
    changes = {"name": "Other.pdf"} if change == "name" else {"data": base64.b64encode(document_pdf(["Other fictional retained bytes"])).decode()}
    code, refused = posted(server, cookie, world, **changes)
    assert code == 400 and "attempt_conflict" in json.dumps(refused)
    assert len(world["store"].uploads(world["client"])) == 1
    assert len(list(world["scope"].queue.glob("*-staff_upload-*.json"))) == 1


def test_other_actor_cannot_observe_receipt_and_unknown_is_truthful(world, server):  # noqa: F811 -- pytest fixture injection
    jane, kim = sign_in(server, "jane@firm.example"), sign_in(server, "kim@firm.example")
    assert posted(server, jane, world)[0] == 200
    assert call(outcome_url(server, world), kim)[0] == 403
    code, raw = call(outcome_url(server, world, "b" * 32), kim)
    assert code == 200 and json.loads(raw) == {"status": "unknown", "received": False, "processed": False}


def test_revoked_access_hides_all_routes_before_effects(world, server):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    restricted.name_person(world["scope"].cases / world["client"], "jane@firm.example", False,
                           world["attorney"]["name"], "attorney", "Jane Fictional")
    assert posted(server, cookie, world)[0] == 404
    hidden = call(outcome_url(server, world), cookie)
    missing = call(server + "/api/staff-upload-outcome?client=no-such-fictional-case&attempt=" + ATTEMPT, cookie)
    assert hidden == missing and hidden[0] == 404
    assert call(server + "/api/source-setup", cookie, {"client": world["client"]})[0] == 404
    no_upload_effects(world)


@pytest.mark.parametrize("mismatch", ["accounts", "documents", "portal", "queue"], ids=["wrong-account-store", "relative-documents", "relative-portal", "relative-queue"])
def test_preflight_mismatch_refuses_without_upload_effects(world, app, server, monkeypatch, mismatch):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    if mismatch == "accounts":
        # A legitimate alternate Accounts store retains this user's session,
        # but cannot stand in for the installed worker's current authority.
        alternate = world["scope"].data / "alternate-review-users.json"
        alternate.write_bytes(app.accounts.path.read_bytes())
        from review.auth import Accounts
        app.accounts = Accounts(alternate)
    elif mismatch == "documents":
        monkeypatch.setenv("I485_CLIENTS_ROOT", "relative-documents")
    elif mismatch == "portal":
        # Same existing profile remains visible at the outer case ACL gate;
        # only its unsafe relative configuration is changed.
        app.portal_root = Path(os.path.relpath(world["scope"].portal))
    else:
        app.jobs_root = Path("relative-jobs")
    code, error = posted(server, cookie, world)
    assert code == 400 and ("configured" in json.dumps(error) or "absolute" in json.dumps(error))
    no_upload_effects(world)


@pytest.mark.parametrize("invalid", ["missing-attempt", "bad-base64", "malformed-pdf"], ids=["missing-attempt", "bad-base64", "malformed-pdf"])
def test_invalid_selection_has_no_receipt_file_job(world, server, invalid):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    changes = {"attempt": None} if invalid == "missing-attempt" else {"data": "not base64!" if invalid == "bad-base64" else base64.b64encode(b"%PDF broken fictional bytes").decode()}
    assert posted(server, cookie, world, **changes)[0] == 400
    no_upload_effects(world)


def test_source_setup_initial_processing_is_current_actor_and_keeps_review_holds(world, server):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    store, client, scope = world["store"], world["client"], world["scope"]
    upload = store.add_upload(client, "i94", "Fictional original.pdf", document_pdf(world["pages"][0].splitlines()), "application/pdf")
    code, raw = call(server + "/api/source-setup", cookie, {"client": client, "actor_email": "forged@fictional.example", "root": "/foreign", "use_policies": False})
    result = json.loads(raw)
    assert code == 200, result
    target = scope.documents / client / "source"
    assert result == {"source_folder": str(target), "associated": True, "review_required": True}
    assert hashlib.sha256((target / upload["stored"]).read_bytes()).hexdigest() == upload["sha256"]
    assert (scope.cases / client / "fact_graph.json").is_file()
    import document_instances, subject_attribution as subjects, critical_review
    assert document_instances.views(scope.cases / client)
    assert subjects.affected_keys(scope.cases / client), "Unassigned retained evidence must remain held."
    from factgraph import FactGraph
    assert critical_review.flags(FactGraph.load(scope.cases / client / "fact_graph_raw.json")), "Setup must not supply named critical approval."
    import source_association
    assert not source_association.hold(scope.cases / client)
    code, second = call(server + "/api/source-setup", cookie, {"client": client})
    assert code == 200 and json.loads(second) == result
    assert len(list(target.glob("*.pdf"))) == 1


def test_registered_worker_reads_once_and_outcome_keeps_review_required(world, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    scope, client = world["scope"], world["client"]
    assert call(server + "/api/source-setup", cookie, {"client": client})[0] == 200
    code, received = posted(server, cookie, world)
    assert code == 200 and received["received"] and not received["processed"]
    # Actual registered handler, explicit synthetic policy setting; HTTP setup
    # above used the production default True and no body policy override.
    context = jobs.Context(scope.cases, scope.portal, jobs_root=scope.queue, use_policies=False)
    job = jobs.get(scope.queue, received["job"]["id"])
    from review import front_desk
    actual = front_desk.read_staff_upload
    calls = []
    def read(*args, **kwargs):
        calls.append(args[2])
        return actual(*args, **kwargs)
    monkeypatch.setattr(front_desk, "read_staff_upload", read)
    done = jobs.run_job(context, job)
    assert done["state"] == "done" and done["result"]["processed"], done
    code, raw = call(outcome_url(server, world), cookie)
    observed = json.loads(raw)
    assert code == 200 and observed["processed"] and observed["review_required"] and not observed["reading"]
    code, retried = posted(server, cookie, world)
    assert code == 200 and retried["processed"] and retried["job"]["id"] == received["job"]["id"]
    assert calls == [client]
    import subject_attribution as subjects, critical_review
    from factgraph import FactGraph
    assert subjects.affected_keys(scope.cases / client)
    assert critical_review.flags(FactGraph.load(scope.cases / client / "fact_graph_raw.json"))
    assert len(list((scope.documents / client / "source").glob("*.pdf"))) == 1


def test_source_setup_missing_preparation_refuses_before_processing(world, server):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    (world["scope"].root / "install/install.json").unlink()
    code, _ = call(server + "/api/source-setup", cookie, {"client": world["client"]})
    assert code == 400
    case = world["scope"].cases / world["client"]
    assert not (case / "fact_graph.json").exists() and not (case / "source-association.json").exists()
    no_upload_effects(world)


def test_deactivated_current_account_cannot_commit(world, server):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    world["accounts"].update("jane@firm.example", active=False, by="Fictional Attorney")
    assert posted(server, cookie, world)[0] == 401
    no_upload_effects(world)


def test_authority_is_reread_inside_helper_not_session_snapshot(world, app, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    real = app._upload_actor_reader
    reads = []
    def revoked(email):
        reads.append(email)
        world["accounts"].update(email, active=False, by="Fictional Attorney")
        return real(email)
    monkeypatch.setattr(app, "_upload_actor_reader", revoked)
    assert posted(server, cookie, world)[0] == 403
    assert reads == ["jane@firm.example"]
    no_upload_effects(world)


def test_reader_busy_upload_and_outcome_are_bounded_and_same_attempt_recovers(world, server):  # noqa: F811 -- pytest fixture injection
    import time
    from staff_upload_lock_fixtures import held_reader
    cookie = sign_in(server, "jane@firm.example")
    with held_reader(world):
        started = time.monotonic()
        code, error = posted(server, cookie, world)
        assert code == 409 and "being read right now" in error["error"]
        assert time.monotonic() - started < 3, "HTTP must not wait for the reader"
        started = time.monotonic()
        code, raw = call(outcome_url(server, world), cookie)
        assert code == 409 and "being read right now" in json.loads(raw)["error"]
        assert time.monotonic() - started < 3, "Outcome observation must not wait for the reader"
        no_upload_effects(world)
    code, received = posted(server, cookie, world)
    assert code == 200 and received["received"] and not received["processed"]
    code, raw = call(outcome_url(server, world), cookie)
    assert code == 200 and json.loads(raw)["job"]["id"] == received["job"]["id"]
    assert len(world["store"].uploads(world["client"])) == 1
    assert len(list(world["scope"].queue.glob("*-staff_upload-*.json"))) == 1
