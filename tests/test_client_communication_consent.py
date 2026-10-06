"""Own-client HTTP signoff; fictional principals/notice, no provider/network."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from fastapi.testclient import TestClient

from portal import app, communication_consent as consent
from test_communication_consent import firm, reviewed, STAFF  # noqa: F401 -- pytest fixture registration and helper reexports


def bootstrap(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    reviewed(firm)
    store.update_profile(client, consent={"email": False, "sms": False, "whatsapp": False})
    token = consent.manual_bootstrap(scope, store, client, actor_email=STAFF)
    session = store.redeem_link(token)
    assert session and store.session_client(session) is None
    return session


def choice(context, channel="email", action="a" * 32):
    option = next(x for x in context["channels"] if x["channel"] == channel)
    return {key: context[key] for key in ("scope", "notice_version", "language", "wording_sha256", "revision")} | {
        "channel": channel, "destination_sha256": option["destination_sha256"], "agree": True,
        "typed_name": "Fictional Client A", "action_id": action}


def http_client(firm, session):  # noqa: F811 -- pytest fixture injection
    scope, _, _ = firm
    http = TestClient(app.create_app(scope.portal, base_url="https://fictional.example"), base_url="https://fictional.example")
    http.cookies.set(app.COOKIE, session)
    return http


def test_actual_http_bootstrap_grant_remains_consent_only_then_separate_accepted_invitation(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    session = bootstrap(firm)
    with http_client(firm, session) as http:
        notice = http.get("/api/communication/consent")
        assert notice.status_code == 200
        context = notice.json()
        assert context["consent_only"] and context["email_control"] is False
        assert context["notice"].startswith("FICTIONAL TEST NOTICE ONLY")
        assert http.get("/api/me").json().get("signed_in") is not True
        assert http.post("/api/submit", json={"agree": True, "signature": "Fictional Signature"}, headers={"X-Portal": "1"}).status_code == 401
        assert not (store.client_dir(client) / consent.FILE).exists()
        body = choice(context)
        assert http.post("/api/communication/consent", json=body).status_code == 403
        accepted = http.post("/api/communication/consent", json=body, headers={"X-Portal": "1"})
        assert accepted.status_code == 200 and accepted.json()["state"] == "granted"
        assert http.get("/api/me").json().get("signed_in") is not True
        duplicate = http.post("/api/communication/consent", json=body, headers={"X-Portal": "1"})
        assert duplicate.status_code == 200 and duplicate.json()["duplicate"]
    row = consent._record(scope, store, client)["channels"]["email"]
    proof = json.loads(consent.evidence(scope, row["evidence"], client=client))
    assert row["actor"] == "client:" + client and row["method"] == "client_portal"
    assert proof["agree"] is True and proof["email_control"] is False and proof["typed_name"] == body["typed_name"]
    assert session not in json.dumps(proof) and len(consent._record(scope, store, client)["history"]) == 1
    tokens = []
    assert consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})["status"] == "sent"
    full = store.redeem_link(tokens[0])
    assert store.session_client(full) == client and store.session_client(session) is None
    with http_client(firm, session) as http:
        revoked = http.post("/api/communication/revoke", json={"scope": "service_notifications", "channels": ["email"]}, headers={"X-Portal": "1"})
        assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
    assert store.session_client(full) is None and store.session_client(session, consent_only=True) is None


@pytest.mark.parametrize("change", ["unchecked", "disagree", "unknown_channel", "wrong_scope", "other_client", "typed_name", "stale_digest", "stale_revision", "wrong_destination"])
def test_explicit_choice_refusal_has_no_signoff_effect(firm, change):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    session = bootstrap(firm)
    with http_client(firm, session) as http:
        body = choice(http.get("/api/communication/consent").json())
        if change == "unchecked":
            body.pop("agree")
        elif change == "disagree":
            body["agree"] = False
        elif change == "unknown_channel":
            body["channel"] = "carrier_pigeon"
        elif change == "wrong_scope":
            body["scope"] = "legal_signature"
        elif change == "other_client":
            body["client"] = "client-b"
        elif change == "typed_name":
            body["typed_name"] = " "
        elif change == "stale_digest":
            body["wording_sha256"] = "0" * 64
        elif change == "stale_revision":
            body["revision"] += 1
        else:
            body["destination_sha256"] = "0" * 64
        assert http.post("/api/communication/consent", json=body, headers={"X-Portal": "1"}).status_code == 409
    assert not (store.client_dir(client) / consent.FILE).exists() and not (store.client_dir(client) / "consent-evidence").exists()


@pytest.mark.parametrize("change", ["contact", "language", "wording", "actor_revoked"])
def test_current_principal_or_notice_changed_after_read_cannot_signoff(firm, change):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    session = bootstrap(firm)
    with http_client(firm, session) as http:
        body = choice(http.get("/api/communication/consent").json())
        if change == "contact":
            store.update_profile(client, email="changed@fictional.example")
        elif change == "language":
            store.update_profile(client, language="pt")
        elif change == "wording":
            path = scope.root / "src/portal/notify.py"
            path.write_bytes(path.read_bytes() + b"\n# fictional wording edit\n")
        else:
            path = scope.data / "review_users.json"
            users = json.loads(path.read_text())
            users["users"][STAFF]["active"] = False
            path.write_text(json.dumps(users))
        assert http.post("/api/communication/consent", json=body, headers={"X-Portal": "1"}).status_code in (401, 409)
    assert not (store.client_dir(client) / consent.FILE).exists()


@pytest.mark.parametrize("cookie", ["", "fictional-staff-session", "fictional-other-client-session"])
def test_staff_or_unbound_general_cookie_cannot_select_client(firm, cookie):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    reviewed(firm)
    with http_client(firm, cookie) as http:
        assert http.get("/api/communication/consent").status_code == 401
        assert http.post("/api/communication/consent", json={"client": client, "agree": True}, headers={"X-Portal": "1"}).status_code == 401
    assert not (store.client_dir(client) / consent.FILE).exists()


def test_prospect_own_cookie_grant_never_creates_main_case_permission(firm):  # noqa: F811 -- pytest fixture injection
    import prospects
    scope, store, client = firm
    pscope = consent.Scope(scope.root, scope.data / "portal/prospects", scope.data / "prospects")
    pstore = prospects.store(scope.portal)
    (pscope.cases / "prospect-a").mkdir(parents=True)
    pstore.add_client("prospect-a", "Fictional Prospect", email="prospect@fictional.example", language="en")
    session = bootstrap((pscope, pstore, "prospect-a"))
    with http_client(firm, session) as http:
        context = http.get("/api/communication/consent").json()
        assert context["channels"][0]["destination"] == "prospect@fictional.example"
        assert http.post("/api/communication/consent", json=choice(context), headers={"X-Portal": "1"}).status_code == 200
    assert consent.eligibility(pscope, pstore, "prospect-a", "email")["allowed"]
    assert not (store.client_dir(client) / consent.FILE).exists() and not (scope.portal / "clients/prospect-a").exists()


def test_same_client_action_concurrency_and_failed_response_preserve_one_grant(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    session = bootstrap(firm)
    body = choice(consent.client_context(scope, store, session))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consent.client_grant(scope, store, session, body), range(2)))
    assert all(row["state"] == "granted" for row in results) and sum(bool(row.get("duplicate")) for row in results) == 1
    assert len(consent._record(scope, store, client)["history"]) == 1
    with pytest.raises(ValueError, match="reused"):
        consent.client_grant(scope, store, session, body | {"typed_name": "Different Fictional Name"})
    assert consent.client_grant(scope, store, session, body)["duplicate"]  # response may have been lost


def test_failed_profile_flag_after_grant_recovers_same_bound_action(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    session = bootstrap(firm)
    body = choice(consent.client_context(scope, store, session))
    atomic = consent._atomic
    def failed(path, value, **kw):
        if path.name == "profile.json" and (value.get("consent") or {}).get("email") is True:
            raise OSError("fictional flag failure after durable grant")
        return atomic(path, value, **kw)
    with monkeypatch.context() as fault:
        fault.setattr(consent, "_atomic", failed)
        with pytest.raises(OSError):
            consent.client_grant(scope, store, session, body)
    assert not consent.eligibility(scope, store, client, "email")["allowed"]
    assert consent.client_grant(scope, store, session, body)["duplicate"]
    assert consent.eligibility(scope, store, client, "email")["allowed"]
    assert len(consent._record(scope, store, client)["history"]) == 1


@pytest.mark.parametrize("store_kind,language", [(kind, lang) for kind in ("main", "prospect") for lang in ("en", "pt", "es", "ht")])
def test_actual_manual_link_opens_own_consent_page_with_reviewed_language_and_no_upgrade(firm, store_kind, language):  # noqa: F811 -- pytest fixture injection
    import prospects
    original_scope, original_store, original_client = firm
    if store_kind == "prospect":
        scope = consent.Scope(original_scope.root, original_scope.data / "portal/prospects", original_scope.data / "prospects")
        store = prospects.store(original_scope.portal)
        client = "prospect-page"
        (scope.cases / client).mkdir(parents=True)
        store.add_client(client, "Fictional Prospect", email="prospect-page@fictional.example", language=language)
    else:
        scope, store, client = firm
        store.update_profile(client, language=language)
    reviewed((scope, store, client), language)
    token = consent.manual_bootstrap(scope, store, client, actor_email=STAFF)
    with TestClient(app.create_app(original_scope.portal, base_url="https://fictional.example"), base_url="https://fictional.example") as http:
        entry = http.get("/l/" + token, follow_redirects=False)
        assert entry.status_code == 303 and entry.headers["location"] == "/consent"
        assert "HttpOnly" in entry.headers["set-cookie"] and "Secure" in entry.headers["set-cookie"]
        page = http.get("/consent")
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        assert 'nonce="%%NONCE%%"' not in page.text and "script-src 'nonce-" in page.headers["content-security-policy"]
        assert 'id="agree" type="checkbox" required' in page.text and 'id="typedName"' in page.text
        assert 'id="withdraw"' in page.text and "localStorage" not in page.text
        assert 'value.notice' in page.text and "$('notice').textContent" in page.text
        context = http.get("/api/communication/consent").json()
        assert context["language"] == language and context["consent_only"] and not context["email_control"]
        assert context["notice"] == "FICTIONAL TEST NOTICE ONLY: " + language
        body = choice(context)
        assert http.post("/api/communication/consent", json=body, headers={"X-Portal": "1"}).status_code == 200
        assert http.get("/api/me").json().get("signed_in") is not True
        assert http.post("/api/submit", json={}, headers={"X-Portal": "1"}).status_code == 401
        assert http.post("/api/communication/revoke", json={"scope": "service_notifications", "channels": ["email", "sms", "whatsapp"]}, headers={"X-Portal": "1"}).status_code == 200
        assert http.get("/consent").status_code == 401
    if store_kind == "prospect":
        assert not (original_store.client_dir(original_client) / consent.FILE).exists()
        assert not (original_scope.portal / "clients" / client).exists()


def test_consent_page_requires_own_current_principal_and_current_reviewed_ui(firm):  # noqa: F811 -- pytest fixture injection
    import client_language_readiness as wording
    scope, store, client = firm
    session = bootstrap(firm)
    with http_client(firm, "fictional-staff-cookie") as http:
        assert http.get("/consent?client=" + client).status_code == 401
    with http_client(firm, session) as http:
        assert http.get("/consent").status_code == 200
        path = scope.root / "src/portal/static/consent.html"
        path.write_bytes(path.read_bytes() + b"\n<!-- fictional changed consent label -->")
        assert not wording.readiness(scope, "en")["ready"]
        assert http.get("/consent").status_code in (401, 409)
    assert not (store.client_dir(client) / consent.FILE).exists()


@pytest.mark.parametrize("name", ["李明", "Al", "A"])
def test_nonempty_short_unicode_name_is_signoff_not_identity_proof(firm, name):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    session = bootstrap(firm)
    with http_client(firm, session) as http:
        body = choice(http.get("/api/communication/consent").json()) | {"typed_name": name}
        assert http.post("/api/communication/consent", json=body, headers={"X-Portal": "1"}).status_code == 200
    proof = json.loads(consent.evidence(scope, consent._record(scope, store, client)["channels"]["email"]["evidence"], client=client))
    assert proof["typed_name"] == name and proof["email_control"] is False
    assert consent.eligibility(scope, store, client, "email")["allowed"]


@pytest.mark.parametrize("language", ["pt", "es", "ht"])
def test_language_switch_retains_saved_answers_and_pending_ev4_recovery_but_reopens_access(firm, monkeypatch, language):  # noqa: F811 -- pytest fixture injection
    import io
    from pypdf import PdfWriter
    from portal import upload_recovery as recovery
    from test_communication_consent import granted
    scope, store, client = firm
    granted(firm)
    saved = {"given_name": "Fictional Maria", "family_name": "Exemplo", "birth_country": "Brazil"}
    store.save_answers(client, saved)
    writer = PdfWriter(); writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO(); writer.write(buffer); document = buffer.getvalue()
    attempt = "e" * 32
    enqueue = store.enqueue
    monkeypatch.setattr(store, "enqueue", lambda *a: (_ for _ in ()).throw(OSError("fictional queue interruption")))
    with pytest.raises(OSError, match="queue interruption"):
        recovery.accept(store, client, attempt, "passport", "fictional.pdf", document, "application/pdf", lambda *a: "tonight")
    monkeypatch.setattr(store, "enqueue", enqueue)
    receipt = recovery._path(store, client, attempt)
    retained = receipt.read_bytes()
    originals = {p.name: p.read_bytes() for p in (store.client_dir(client) / "uploads").iterdir()}
    rows = store.uploads(client)
    assert len(rows) == len(originals) == 1
    assert recovery.outcome(store, client, attempt, lambda *a: "tonight", repair=False)["status"] == "pending"
    tokens = []
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    session = store.redeem_link(tokens[0])
    with http_client(firm, session) as http:
        result = http.post("/api/language", json={"language": language}, headers={"X-Portal": "1"})
        assert result.status_code == 200 and result.json()["language"] == language
        assert result.json()["answers"] == saved
        assert http.get("/api/me").status_code == 401
        assert http.get("/api/upload-outcome", params={"attempt": attempt}).status_code == 401
    assert store.answers(client) == saved and store.uploads(client) == rows
    assert receipt.read_bytes() == retained
    assert {p.name: p.read_bytes() for p in (store.client_dir(client) / "uploads").iterdir()} == originals
    held = []
    assert consent.dispatch(scope, store, client, "email", lambda *a: held.append(a))["status"] == "held"
    assert not held
    # Review the actual new-language bundle, then own explicit signoff. Neither
    # operation restores the previous credential or grants general access.
    reviewed(firm, language)
    assisted = consent.manual_bootstrap(scope, store, client, actor_email=STAFF)
    signoff = store.redeem_link(assisted)
    with http_client(firm, signoff) as http:
        context = http.get("/api/communication/consent").json()
        assert context["language"] == language and context["consent_only"]
        assert http.post("/api/communication/consent", json=choice(context), headers={"X-Portal": "1"}).status_code == 200
        assert http.get("/api/me").status_code == 401
    tokens.clear()
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    current = store.redeem_link(tokens[0])
    monkeypatch.setenv("PORTAL_READ_AT_ONCE", "0")
    with http_client(firm, current) as http:
        assert http.get("/api/me").json()["answers"] == saved
        recovered = http.get("/api/upload-outcome", params={"attempt": attempt})
        assert recovered.status_code == 200 and recovered.json()["upload_outcome"]["status"] == "complete"
        assert recovered.json()["upload_outcome"]["id"] == rows[0]["id"]
    assert store.answers(client) == saved
    # Completion may add only the canonical reading outcome; original identity,
    # document/source hashes, bytes, size and upload timestamp remain exact.
    assert store.uploads(client) == [dict(rows[0], status="received", reading="tonight")]
    assert {p.name: p.read_bytes() for p in (store.client_dir(client) / "uploads").iterdir()} == originals
    assert store.session_client(session) is None
