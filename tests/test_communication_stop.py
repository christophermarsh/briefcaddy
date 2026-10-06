"""Real official SDK verifier, fictional signed forms, installed worker retry.

There is no REST client or network operation. Fixture keys cannot be real keys.
"""
import json
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import jobs
import oslock
from portal import app, communication_consent as consent, opt_out
from test_communication_consent import firm, granted, reviewed, STAFF  # noqa: F401 -- pytest fixture registration and helper reexports

KEY = "fictional-local-validator-key-not-a-production-token"
URL = "https://fictional.example/api/communication/stop/twilio"
ACCOUNT = "AC" + "1" * 32
MESSAGE = "SM" + "2" * 32


@pytest.fixture
def config(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    values = {"TWILIO_ACCOUNT_SID": ACCOUNT, "TWILIO_AUTH_TOKEN": KEY, "TWILIO_SMS_FROM": "+16175550999",
              "TWILIO_WHATSAPP_FROM": "+16175550888", "TWILIO_STOP_WEBHOOK_URL": URL}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def signed(config, **changes):
    values = {"AccountSid": ACCOUNT, "MessageSid": MESSAGE, "From": "+16175550101", "To": config["TWILIO_SMS_FROM"],
              "Body": "STOP", "OptOutType": "STOP", "EvolvingProviderField": "signed-all-parameters"} | changes
    return values, urlencode(values).encode(), RequestValidator(KEY).compute_signature(URL, values)


def test_http_signature_includes_all_fields_and_invalid_signature_has_no_effect(firm, config):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    values, body, signature = signed(config)
    portal = app.create_app(scope.portal, base_url="https://fictional.example")
    with TestClient(portal) as http:
        bad = http.post("/api/communication/stop/twilio", content=body, headers={"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": "bad"})
        assert bad.status_code == 403
        assert not (opt_out.folder(scope) / "queue.json").exists()
        # No X-Portal header: only this exact route uses SDK authentication.
        accepted = http.post("/api/communication/stop/twilio", content=body, headers={"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signature})
        assert accepted.status_code == 200 and accepted.text == "<Response/>"
        assert accepted.headers["X-Communication-State"] == "applied"
        assert store.profile(client)["phone"] not in accepted.text
        ordinary = http.post("/api/link", json={"contact": "a@fictional.example"})
        assert ordinary.status_code == 403  # ordinary portal guard unchanged


@pytest.mark.parametrize("change", ["account", "recipient", "url", "body", "duplicate"])
def test_untrusted_account_url_recipient_form_or_payload_rejected(firm, config, change):  # noqa: F811 -- pytest fixture injection
    scope, _, _ = firm
    values, body, signature = signed(config)
    settings = dict(config)
    if change == "account":
        settings["TWILIO_ACCOUNT_SID"] = "AC" + "3" * 32
    elif change == "recipient":
        settings["TWILIO_SMS_FROM"] = "+16175550777"
    elif change == "url":
        settings["TWILIO_STOP_WEBHOOK_URL"] = "https://foreign.example/api/communication/stop/twilio"
    elif change == "body":
        body += b"&UnsignedNewField=forged"
    else:
        body += b"&Body=STOP"
    with pytest.raises((ValueError, PermissionError)):
        opt_out.ingest(scope, body, signature, env=settings)
    assert not (opt_out.folder(scope) / "queue.json").exists()


def test_stop_shared_destination_revokes_main_prospect_and_invalidates_existing_email_sessions(firm, config):  # noqa: F811 -- pytest fixture injection
    import prospects
    scope, store, client = firm
    granted(firm)
    granted(firm, "sms")
    tokens = []
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    session = store.redeem_link(tokens[0])
    assert session
    pscope = consent.Scope(scope.root, scope.data / "portal/prospects", scope.data / "prospects")
    pstore = prospects.store(scope.portal)
    (pscope.cases / "prospect-a").mkdir(parents=True)
    pstore.add_client("prospect-a", "Fictional Prospect", email="prospect@fictional.example", phone="+16175550101", language="en")
    granted((pscope, pstore, "prospect-a"), "sms")
    _, body, signature = signed(config)
    result = opt_out.ingest(scope, body, signature, env=config)
    assert result["state"] == "applied"
    receipt = opt_out._receipt(scope, result["receipt"])
    assert {(m["store"], m["client"]) for m in receipt["members"]} == {("client", client), ("prospect", "prospect-a")}
    assert all(m["state"] == "applied" for m in receipt["members"])
    assert consent._record(scope, store, client)["channels"]["sms"]["state"] == "revoked"
    assert consent._record(pscope, pstore, "prospect-a")["channels"]["sms"]["state"] == "revoked"
    assert store.session_client(session) is None
    raw = (opt_out.folder(scope) / "receipts" / (result["receipt"] + ".json")).read_text()
    assert "+16175550101" not in raw and KEY not in raw and '"Body"' not in raw
    assert "payload_sha256" in raw and receipt["installation"] == str(scope.root)


def test_authenticated_duplicate_is_idempotent_and_reused_sid_refuses_changed_payload(firm, config):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm, "sms")
    _, body, signature = signed(config)
    result = opt_out.ingest(scope, body, signature, env=config)
    old = consent._record(scope, store, client)
    repeat = opt_out.ingest(scope, body, signature, env=config)
    assert repeat["duplicate"] and repeat["receipt"] == result["receipt"]
    assert consent._record(scope, store, client) == old
    _, changed, signature = signed(config, Body="STOPALL")
    with pytest.raises(ValueError, match="reused"):
        opt_out.ingest(scope, changed, signature, env=config)


def test_no_start_or_help_grant_and_whatsapp_stop_binding(firm, config):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    for word in ("START", "HELP"):
        _, body, signature = signed(config, Body=word, OptOutType=word)
        assert opt_out.ingest(scope, body, signature, env=config)["state"] == "ignored"
    assert not (store.client_dir(client) / consent.FILE).exists()
    granted(firm, "whatsapp")
    _, body, signature = signed(config, From="whatsapp:+16175550101", To="whatsapp:" + config["TWILIO_WHATSAPP_FROM"])
    assert opt_out.ingest(scope, body, signature, env=config)["state"] == "applied"
    assert consent._record(scope, store, client)["channels"]["whatsapp"]["state"] == "revoked"
    assert not consent.eligibility(scope, store, client, "whatsapp")["allowed"]  # channel remains disabled


def test_failed_intent_persistence_is_not_http_acknowledged(firm, config, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, _, _ = firm
    _, body, signature = signed(config)
    monkeypatch.setattr(opt_out, "_atomic", lambda *a, **kw: (_ for _ in ()).throw(OSError("fictional disk failure")))
    portal = app.create_app(scope.portal, base_url="https://fictional.example")
    with TestClient(portal) as http:
        result = http.post("/api/communication/stop/twilio", content=body,
                           headers={"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signature})
    assert result.status_code == 503 and not (opt_out.folder(scope) / "queue.json").exists()


def test_pending_receipt_denies_before_actual_worker_retry_without_redelivery(firm, config, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    granted(firm, "sms")
    tokens = []
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    session = store.redeem_link(tokens[0])
    _, body, signature = signed(config)
    with monkeypatch.context() as held:
        held.setattr(opt_out, "process", lambda *a, **kw: (_ for _ in ()).throw(oslock.LockBusy("fictional provider-first gate holder")))
        portal = app.create_app(scope.portal, base_url="https://fictional.example")
        with TestClient(portal) as http:
            received = http.post("/api/communication/stop/twilio", content=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signature})
    assert received.status_code == 200 and received.headers["X-Communication-State"] == "pending"
    assert opt_out.pending_for(scope, store.profile(client))
    assert store.session_client(session) is None
    called = []
    def next_job(ctx, job, progress):
        assert not opt_out.pending_for(scope, store.profile(client))
        assert consent._record(scope, store, client)["channels"]["sms"]["state"] == "revoked"
        called.append(job["id"])
        return {"processed": True}
    monkeypatch.setitem(jobs.HANDLERS, "inbox_read", next_job)
    queued = jobs.submit(scope.data / "jobs", "inbox_read", client, by="Fictional Worker Probe")
    # No second provider webhook call: canonical installed worker consumes proof.
    assert jobs.work(scope.cases, scope.portal, jobs_root=scope.data / "jobs", once=True, log=lambda _: None) == 1
    assert called == [queued["id"]]
    assert opt_out._index(scope)["pending"] == []


def test_fault_after_revoke_before_receipt_progress_does_not_append_duplicate_history(firm, config, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm, "sms")
    _, body, signature = signed(config)
    atomic = opt_out._atomic
    def fail(path, value, **kw):
        if "receipts" in path.parts and value.get("state") == "pending" and any(m["state"] == "applied" for m in value.get("members", [])):
            raise OSError("fictional post-effect receipt failure")
        return atomic(path, value, **kw)
    with monkeypatch.context() as fault:
        fault.setattr(opt_out, "_atomic", fail)
        result = opt_out.ingest(scope, body, signature, env=config)
    assert result["state"] == "pending"
    record = consent._record(scope, store, client)
    operation_rows = [r for r in record["history"] if r.get("operation_id") == result["receipt"]]
    assert len(operation_rows) == 1
    opt_out.pump(jobs.Context(scope.cases, scope.portal, jobs_root=scope.data / "jobs"))
    assert opt_out._receipt(scope, result["receipt"])["state"] == "applied"
    assert len([r for r in consent._record(scope, store, client)["history"] if r.get("operation_id") == result["receipt"]]) == 1


def test_applied_receipt_cleanup_fault_stays_queued_for_worker(firm, config, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm, "sms")
    _, body, signature = signed(config)
    atomic = opt_out._atomic
    def fail(path, value, **kw):
        if path.parent.name == "destinations" and value.get("pending") == []:
            raise OSError("fictional pending-index cleanup failure")
        return atomic(path, value, **kw)
    with monkeypatch.context() as fault:
        fault.setattr(opt_out, "_atomic", fail)
        result = opt_out.ingest(scope, body, signature, env=config)
    assert result["receipt"] in opt_out._index(scope)["pending"]
    assert opt_out.pending_for(scope, store.profile(client))
    opt_out.pump(jobs.Context(scope.cases, scope.portal, jobs_root=scope.data / "jobs"))
    assert not opt_out.pending_for(scope, store.profile(client)) and opt_out._index(scope)["pending"] == []


def test_corrupt_retry_proof_is_unresolved_and_does_not_starve_next_job(firm, config, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    _, body, signature = signed(config)
    result = opt_out.ingest(scope, body, signature, env=config, apply_now=False)
    path = opt_out.folder(scope) / "receipts" / (result["receipt"] + ".json")
    path.write_text("{")
    output = opt_out.pump(jobs.Context(scope.cases, scope.portal, jobs_root=scope.data / "jobs"))
    assert output["unresolved"] == 1 and output["pending"] == 1
    assert not consent.eligibility(scope, store, client, "email")["allowed"]
    called, messages = [], []
    monkeypatch.setitem(jobs.HANDLERS, "inbox_read", lambda ctx, job, progress: called.append(job["id"]) or {})
    queued = jobs.submit(scope.data / "jobs", "inbox_read", client, by="Fictional Work")
    assert jobs.work(scope.cases, scope.portal, jobs_root=scope.data / "jobs", once=True, log=messages.append) == 1
    assert called == [queued["id"]] and any("remain" in text for text in messages)


def test_two_thousand_current_profiles_late_match_is_revoked_without_truncation(firm, config):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    store.update_profile(client, phone="+16175550777")
    unrelated = store.profile(client)
    unrelated_permission = consent._record(scope, store, client)
    # Exact 2000-profile inventory; direct fictional seeding avoids 1999 audit
    # writes unrelated to the actual SDK/worker acceptance node.
    for number in range(1999):
        cid = f"zz-fictional-{number:04d}"
        folder = store.client_dir(cid)
        folder.mkdir(parents=True)
        profile = {"id": cid, "name": "Fictional Inventory", "email": f"inventory-{number}@fictional.example",
                   "phone": "+16175550101" if number == 1998 else "+16175550666", "language": "en", "consent": {"sms": False}}
        (folder / "profile.json").write_text(json.dumps(profile))
    last = "zz-fictional-1998"
    (scope.cases / last).mkdir()
    assert len(store.clients()) == 2000 and store.clients()[-1] == last
    _, body, signature = signed(config)
    received = opt_out.ingest(scope, body, signature, env=config, apply_now=False)
    assert opt_out.pump(jobs.Context(scope.cases, scope.portal, jobs_root=scope.data / "jobs"))["processed"] == 1
    receipt = opt_out._receipt(scope, received["receipt"])
    assert receipt["members"] == [{"store": "client", "client": last, "state": "applied"}]
    assert consent._record(scope, store, last)["channels"]["sms"]["state"] == "revoked"
    assert store.profile(client) == unrelated and consent._record(scope, store, client) == unrelated_permission
    assert not (store.client_dir("zz-fictional-0000") / consent.FILE).exists()
