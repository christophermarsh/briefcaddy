"""Fictional HTTP -> real installed Drive adapter -> registered local worker.

Every provider request is handled by MockTransport. No reader labels or
approval outcomes are supplied to the product pipeline.
"""
import hashlib
import json

import httpx
import pytest
import document_instances
import documents
import firmsecrets
import jobs
import restricted
from connectors import drive_intake, drive_settings, sync
from connectors.base import RemoteDoc
from factgraph import FactGraph
from portal.demo import document_pdf
from drive_workflow_fixtures import source_firm, world, drive_world, configured_world, app, server, call, sign_in, ATTORNEY  # noqa: F401 -- pytest fixture registration and helper reexports

REMOTE = "remote-fictional"


def configure(w, mapping=None, revision=0, attempt="c" * 32):
    return drive_settings.Settings(w["scope"]).save(ATTORNEY, expected_revision=revision, operation_id=attempt,
        supplied_provider={"root_folder_id": "mocked-configured-root", "documents_subfolder": None},
        supplied_bindings=mapping if mapping is not None else {REMOTE: w["client"]})


def answer(server, cookie, route, body=None):  # noqa: F811 -- pytest fixture injection
    status, raw = call(server + route, cookie, body)
    return status, json.loads(raw)


def shown(server, cookie, w):  # noqa: F811 -- pytest fixture injection
    status, result = answer(server, cookie, "/api/drive-preview", {"client": w["client"], "selected": [REMOTE]})
    assert status == 200, result
    return result


@pytest.fixture
def provider(configured_world, monkeypatch):  # noqa: F811 -- pytest fixture injection
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    w = configured_world
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    secret = json.dumps({"client_email": "fictional-service@invalid.example", "private_key": key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
        "token_uri": "https://fictional-token.invalid/token"})
    def vault(name, **kw):
        assert name == "gdrive.service_account" and kw["data_root"] == w["scope"].data and kw["env"] == {}
        return secret
    monkeypatch.setattr(firmsecrets, "get", vault)
    pdf = document_pdf((w["pages"][1] + "\nRetained fictional Drive original").splitlines())
    state = {"calls": [], "downloads": 0, "pdf": pdf, "listed": [], "name": "Drive evidence.pdf"}
    def transport(request):
        state["calls"].append((request.method, request.url.host, request.url.path))
        if request.url.host == "fictional-token.invalid":
            assert request.method == "POST" and b"jwt-bearer" in request.content
            return httpx.Response(200, json={"access_token": "fictional-token", "expires_in": 3600})
        assert request.method == "GET" and request.url.host == "www.googleapis.com"
        if request.url.path == "/drive/v3/files":
            query = request.url.params["q"]
            state["listed"].append(query)
            if "'mocked-configured-root'" in query:
                return httpx.Response(200, json={"files": [
                    {"id": REMOTE, "name": "Fictional selected person", "mimeType": "application/vnd.google-apps.folder"},
                    {"id": "remote-other-private", "name": "Other fictional private person", "mimeType": "application/vnd.google-apps.folder"}]})
            assert "'" + REMOTE + "'" in query, "Never list the other client's documents"
            return httpx.Response(200, json={"files": [{"id": "fictional-pdf", "name": state["name"],
                "mimeType": "application/pdf", "size": str(len(pdf)), "modifiedTime": "2026-10-05T00:00:00Z",
                "md5Checksum": hashlib.md5(pdf).hexdigest()}]})
        assert request.url.path == "/drive/v3/files/fictional-pdf" and request.url.params["alt"] == "media"
        state["downloads"] += 1
        return httpx.Response(200, content=pdf)
    mock = httpx.MockTransport(transport)
    original = drive_intake.installed
    monkeypatch.setattr(drive_intake, "installed", lambda scope: original(scope, transport=mock))
    monkeypatch.setattr(jobs, "ensure_worker", lambda *_a, **_k: False)
    return state


def test_case_selection_is_readonly_bounded_and_disabled_when_unconfigured(configured_world, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    cookie = sign_in(server, "jane@firm.example")
    monkeypatch.setattr(drive_intake, "installed", lambda *_: pytest.fail("Selection GET cannot open provider"))
    status, result = answer(server, cookie, "/api/drive-selection?client=" + w["client"])
    assert status == 200 and not result["configured"] and not result["ready"] and result["selected"] == []
    configure(w)
    status, result = answer(server, cookie, "/api/drive-selection?client=" + w["client"])
    assert status == 200 and result["selected"] == [REMOTE] and result["ready"]
    assert set(result) == {"client", "revision", "selected", "configured", "ready", "setup_required", "audit_pending", "reason"}
    assert not list(w["scope"].queue.glob("*.json"))


@pytest.mark.parametrize("active", [None, "yes", 1], ids=["missing-active", "string-active", "integer-active"])
def test_selection_requires_canonical_active_boolean(drive_world, app, monkeypatch, active):  # noqa: F811 -- pytest fixture injection
    w = drive_world
    actor = {"email": ATTORNEY, "name": "Pat Drive Attorney", "role": "attorney"}
    if active is not None:
        actor["active"] = active
    monkeypatch.setattr(app, "_upload_actor_reader", lambda _: actor)
    monkeypatch.setattr(drive_intake, "installed", lambda *_: pytest.fail("Malformed authority cannot open provider"))
    with pytest.raises(PermissionError):
        app.drive_selection(w["client"], actor)
    assert not drive_settings.paths(w["scope"])[0].exists() and not list(w["scope"].queue.glob("*.json"))


def test_hidden_and_nonexistent_case_are_same_response_before_provider(configured_world, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    cookie = sign_in(server, "kim@firm.example")
    restricted.name_person(w["scope"].cases / w["client"], "kim@firm.example", False, "Fictional Attorney", "attorney", "Kim Fictional")
    monkeypatch.setattr(drive_intake, "installed", lambda *_: pytest.fail("Hidden cases cannot open provider"))
    for route, data in [("/api/drive-selection?client=", None), ("/api/drive-preview", {"selected": [REMOTE]}),
                        ("/api/drive-enqueue", {"preview": {"snapshot": {}}, "attempt": "d" * 32})]:
        replies = []
        for case in (w["client"], "missing-fictional"):
            replies.append(call(server + route + case if data is None else server + route,
                                cookie, {"client": case, **data} if data is not None else None))
        assert replies[0] == replies[1] and replies[0][0] == 404


def test_preview_only_current_configured_case_no_download_or_jobs(configured_world, server, provider):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    configure(w)
    cookie = sign_in(server, "jane@firm.example")
    preview = shown(server, cookie, w)
    snapshot = preview["snapshot"]
    assert preview["writes_to_drive"] is False and preview["processing"] == "not queued"
    assert snapshot["mapping"] == {REMOTE: w["client"]} and len(snapshot["clients"]) == 1
    assert "remote-other-private" not in json.dumps(preview) and "Other fictional private" not in json.dumps(preview)
    assert provider["downloads"] == 0 and not list(w["scope"].queue.glob("*.json"))
    assert not list((w["scope"].documents / w["client"] / "source").glob("*.pdf"))


@pytest.mark.parametrize("selected", [None, [], ["remote-other-private"], [REMOTE, REMOTE], "remote-fictional"],
                         ids=["missing", "empty", "other-case", "duplicate", "string"])
def test_invalid_selection_never_contacts_provider(configured_world, server, monkeypatch, selected):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    configure(w)
    cookie = sign_in(server, "jane@firm.example")
    monkeypatch.setattr(drive_intake, "installed", lambda *_: pytest.fail("Invalid selection cannot open provider"))
    status, _ = answer(server, cookie, "/api/drive-preview", {"client": w["client"], "selected": selected,
        "mapping": {"remote-other-private": w["client"]}, "root": "/forged", "role": "attorney"})
    assert status == 400 and not list(w["scope"].queue.glob("*.json"))


def test_enqueue_revalidates_preview_same_attempt_and_current_configuration(configured_world, app, server, provider):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    configure(w)
    cookie = sign_in(server, "jane@firm.example")
    preview = shown(server, cookie, w)
    body = {"client": w["client"], "preview": preview, "attempt": "d" * 32, "actor": ATTORNEY}
    status, result = answer(server, cookie, "/api/drive-enqueue", body)
    assert status == 200 and result["completed"] is False and result["worker_available"] is False
    assert answer(server, cookie, "/api/drive-enqueue", body)[1] == result
    jid = result["jobs"][REMOTE]["id"]
    rows = [j for j in jobs.jobs(w["scope"].queue, client=w["client"]) if j["kind"] == "drive_intake"]
    assert len(rows) == 1 and rows[0]["id"] == jid and rows[0]["args"]["actor"] == "jane@firm.example"
    configure(w, {}, revision=1, attempt="e" * 32)
    assert answer(server, cookie, "/api/drive-enqueue", body)[0] == 400
    calls = len(provider["calls"])
    ctx = jobs.Context(w["scope"].cases, w["scope"].portal, jobs_root=w["scope"].queue, use_policies=False)
    failed = jobs.run_job(ctx, jobs.get(w["scope"].queue, jid))
    assert failed["state"] == "failed" and len(provider["calls"]) == calls and provider["downloads"] == 0


def test_forged_cross_case_snapshot_refused_before_installed_helper(configured_world, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    configure(w)
    cookie = sign_in(server, "jane@firm.example")
    monkeypatch.setattr(drive_intake, "installed", lambda *_: pytest.fail("Cross-case snapshot cannot open provider"))
    status, _ = answer(server, cookie, "/api/drive-enqueue", {"client": w["client"], "attempt": "d" * 32,
        "preview": {"snapshot": {"selected": [REMOTE], "mapping": {REMOTE: "missing-fictional"}}}})
    assert status == 400 and not list(w["scope"].queue.glob("*.json"))


def test_actual_http_registered_worker_and_later_portal_preserve_evidence_holds(configured_world, app, server, provider):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    configure(w)
    cookie = sign_in(server, "jane@firm.example")
    preview = shown(server, cookie, w)
    status, queued = answer(server, cookie, "/api/drive-enqueue", {"client": w["client"], "preview": preview, "attempt": "d" * 32})
    assert status == 200
    jid = queued["jobs"][REMOTE]["id"]
    # Actual installed work loop and registered drive_intake handler. Policies
    # disabled only as an explicit synthetic runtime control, not an approval.
    assert jobs.work(w["scope"].cases, w["scope"].portal, once=True, jobs_root=w["scope"].queue, use_policies=False) >= 1
    job = jobs.get(w["scope"].queue, jid)
    assert job["state"] == "done", job.get("error")
    processing = job["result"]["processing"]
    assert processing["state"] in {"completed", "held"} and processing["result"]["processed"]
    assert processing["result"]["review_required"] and processing["result"]["signed"] is False and processing["result"]["sent"] is False
    drive_row = job["args"]["preview"]["snapshot"]["clients"][0]["documents"][0]
    filename = sync._filename(RemoteDoc(drive_row["id"], drive_row["name"], drive_row["mime"]))
    source = w["scope"].documents / w["client"] / "source"
    assert (source / filename).read_bytes() == provider["pdf"] and provider["downloads"] == 1
    graph_before = FactGraph.load(w["scope"].cases / w["client"] / "fact_graph_raw.json")
    old_values = {key: [s.normalized_value for s in f.sources if s.doc_id.split("#p", 1)[0] == filename]
                  for key, f in graph_before.all_facts().items() if any(s.doc_id.split("#p", 1)[0] == filename for s in f.sources)}
    assert old_values
    later_bytes = document_pdf((w["pages"][0] + "\nLater fictional portal original").splitlines())
    later = w["store"].add_upload(w["client"], "i94", "later.pdf", later_bytes, "application/pdf")
    w["store"].save_answers(w["client"], {"given_name": "UPDATED"})
    w["store"].enqueue(w["client"])
    assert jobs.work(w["scope"].cases, w["scope"].portal, once=True, jobs_root=w["scope"].queue, use_policies=False) >= 1
    assert (source / filename).read_bytes() == provider["pdf"] and (source / later["stored"]).read_bytes() == later_bytes
    after = FactGraph.load(w["scope"].cases / w["client"] / "fact_graph_raw.json")
    for key, values in old_values.items():
        assert [s.normalized_value for s in after.get(key).sources if s.doc_id.split("#p", 1)[0] == filename] == values
    rows = documents.read(w["scope"].cases / w["client"])["documents"]
    assert next(row for row in rows if filename in row["files"])["source"] == "drive"
    assert next(row for row in rows if later["stored"] in row["files"])["source"] == "portal"
    views = document_instances.views(w["scope"].cases / w["client"])
    assert views and all(not row["stale"] and not row.get("processing_incomplete") for row in views)
    assert all(row.get("reviewed_by") is None for row in views)  # reading is not boundary approval
    assert provider["downloads"] == 1  # later portal work does not redownload Drive
