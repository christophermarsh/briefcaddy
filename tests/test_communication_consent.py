"""Fictional approval artifacts/accounts only; no providers or client messages."""
from datetime import timedelta
import json
from pathlib import Path
import shutil
import threading

import pytest

import client_language_readiness as wording
from portal import communication_consent as consent
from portal.store import PortalStore, _hash, _now
from rules import approval

REPO = Path(__file__).resolve().parents[1]
ATTORNEY = "attorney@fictional.example"
STAFF = "staff@fictional.example"


@pytest.fixture
def firm(tmp_path, monkeypatch):
    root = tmp_path / "fictional-consent-firm"
    for name in wording.SOURCES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
    data = root / "data"
    data.mkdir()
    for key, target in {"I485_RULES_APPROVED": data / "rules_approved.json", "I485_MAINTENANCE_LOG": data / "maintenance_log.json",
                        "I485_EVENTS": data / "events.jsonl", "I485_JOBS": data / "jobs", "I485_CASES": data / "clients",
                        "PORTAL_DATA": data / "portal", "I485_SETTINGS": data / "settings.json"}.items():
        monkeypatch.setenv(key, str(target))
    import settings
    monkeypatch.setattr(settings, "PATH", data / "settings.json")
    monkeypatch.setattr(wording, "ROOT", root)
    (data / "communication_notice.json").write_text(json.dumps({"schema_version": 1, "version": "fictional-notice-v1",
        "texts": {lang: "FICTIONAL TEST NOTICE ONLY: " + lang for lang in wording.LANGUAGES}}))
    accounts = {ATTORNEY: {"name": "Fictional Attorney", "role": "attorney", "active": True},
                STAFF: {"name": "Fictional Staff", "role": "paralegal", "active": True}}
    (data / "review_users.json").write_text(json.dumps({"users": accounts, "sessions": {}}))
    scope = consent.Scope(root, data / "portal", data / "clients")
    (scope.cases / "client-a").mkdir(parents=True)
    store = PortalStore(scope.portal)
    store.add_client("client-a", "Fictional Client A", email="a@fictional.example", phone="+16175550101", language="en")
    return scope, store, "client-a"


def artifact(firm, kind="wording", actor=ATTORNEY, label="Fictional review evidence"):
    scope, store, client = firm
    return consent.retain_evidence(scope, json.dumps({"fictional": True, "statement": label}).encode(), actor_email=actor,
                                   kind=kind, store=store if kind == "consent" else None, client=client if kind == "consent" else None)


def reviewed(firm, language="en"):
    scope, _, _ = firm
    ref = artifact(firm)
    wording.review_attorney(scope, language, actor_email=ATTORNEY, evidence_ref=ref)
    if language != "en":
        wording.review_translation(scope, language, actor_email=ATTORNEY, reviewer_name="Fictional Qualified Reviewer",
                                   qualification="Fictional actual review qualification evidence", evidence_ref=ref)
    return ref


def granted(firm, channel="email", actor=STAFF):
    scope, store, client = firm
    reviewed(firm, store.profile(client)["language"])
    ref = artifact(firm, "consent", actor)
    row = consent.grant(scope, store, client, channel, actor_email=actor, evidence_ref=ref,
                        client_approved_at=(_now() - timedelta(minutes=1)).isoformat(), notice_version="fictional-notice-v1",
                        language=store.profile(client)["language"], source_kind="in_person",
                        approval_description="Fictional client actually approved service messages in person; test artifact only.")
    return row


def entry(store, token):
    return store._auth()["links"][_hash(token)]


def test_import_booleans_and_missing_grant_never_dispatch(firm):
    scope, store, client = firm
    reviewed(firm)
    assert store.profile(client)["consent"]["email"] is True
    auth_before = (scope.portal / "auth.json").read_bytes()
    assert store._auth() == {"links": {}, "sessions": {}}
    calls = []
    result = consent.dispatch(scope, store, client, "email", lambda *args: calls.append(args))
    assert result["status"] == "held" and not calls
    assert (scope.portal / "auth.json").read_bytes() == auth_before
    assert store._auth() == {"links": {}, "sessions": {}}
    assert not (store.client_dir(client) / "communication-attempts").exists()


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
def test_phone_channels_require_current_verified_control_after_actual_signoff(firm, channel):
    scope, store, client = firm
    granted(firm, channel)
    calls = []
    assert consent.dispatch(scope, store, client, channel, lambda *a: calls.append(a))["reason"] == "contact_control_required"
    assert calls == []


def test_documented_grant_migrates_false_flag_and_preserves_real_provenance(firm):
    scope, store, client = firm
    store.update_profile(client, consent={"email": False})
    row = granted(firm)
    assert row["method"] == "documented_client_approval" and row["actor"] == STAFF
    assert consent.eligibility(scope, store, client, "email")["allowed"]
    assert store.profile(client)["consent"]["email"] is True
    assert consent._record(scope, store, client)["history"] == [{k: v for k, v in row.items() if k != "audit_status"}]


def test_stale_contact_and_language_reject_before_provider(firm):
    scope, store, client = firm
    granted(firm)
    store.update_profile(client, email="changed@fictional.example")
    assert consent._record(scope, store, client)["channels"]["email"]["state"] == "revoked"
    assert consent.eligibility(scope, store, client, "email")["reason"] == "actual_client_signoff_required"
    store.update_profile(client, email="a@fictional.example", language="ht")
    assert not consent.eligibility(scope, store, client, "email")["allowed"]


@pytest.mark.parametrize("change", ["future", "nonstring", "revision", "actor", "description", "history", "role", "unbound_enrollment", "foreign_enrollment"])
def test_malformed_granted_row_never_dispatches(firm, change):
    scope, store, client = firm
    granted(firm)
    path = store.client_dir(client) / consent.FILE
    record = json.loads(path.read_text())
    row = record["channels"]["email"]
    if change == "future":
        row["client_approved_at"] = (_now() + timedelta(days=1)).isoformat()
    elif change == "nonstring":
        row["client_approved_at"] = []
    elif change == "revision":
        row["revision"] = record["revision"] + 1
    elif change == "actor":
        row["actor"] = {"email": STAFF}
    elif change == "description":
        row["approval_description"] = ""
    elif change == "role":
        row["actor_role"] = "support"
    elif change == "unbound_enrollment":
        row.pop("enrollment_sha256")
    elif change == "foreign_enrollment":
        row["enrollment_sha256"] = "a" * 64
    else:
        record["history"] = []
    if change != "history":
        record["history"][-1] = dict(row)  # also exercise structural guards
    path.write_text(json.dumps(record))
    assert consent.dispatch(scope, store, client, "email", lambda *a: pytest.fail("malformed grant dispatched"))["status"] == "held"


@pytest.mark.parametrize("change", ["corrupt", "missing", "evidence"])
def test_damaged_or_missing_permission_evidence_denies(firm, change):
    scope, store, client = firm
    row = granted(firm)
    path = store.client_dir(client) / consent.FILE
    if change == "corrupt":
        path.write_text("{")
    elif change == "missing":
        path.unlink()
    else:
        consent._evidence_path(scope, row["evidence"], client=client).write_bytes(b"changed")
    assert not consent.eligibility(scope, store, client, "email")["allowed"]
    if change == "corrupt":
        with pytest.raises(ValueError):
            granted(firm)
        assert path.read_text() == "{"


@pytest.mark.parametrize("change", ["deactivated", "access_removed"])
def test_historical_recorder_turnover_preserves_client_signoff_not_current_staff_authority(firm, monkeypatch, change):
    import restricted
    scope, store, client = firm
    row = granted(firm)
    before = consent._record(scope, store, client)
    if change == "deactivated":
        path = scope.data / "review_users.json"
        accounts = json.loads(path.read_text())
        accounts["users"][STAFF]["active"] = False
        path.write_text(json.dumps(accounts))
    else:
        visible = restricted.visible_to
        monkeypatch.setattr(restricted, "visible_to", lambda user, case: False if user.get("email") == STAFF else visible(user, case))
    with pytest.raises(PermissionError):
        consent.staff(scope, STAFF, case=scope.cases / client)
    # A current authorized colleague can act; historical recording identity is
    # never reused as authority. Automatic eligibility uses current case/notice.
    assert consent.staff(scope, ATTORNEY, case=scope.cases / client)["active"]
    calls = []
    assert consent.dispatch(scope, store, client, "email", lambda *a: calls.append(a) or {"status": "sent"})["status"] == "sent"
    assert len(calls) == 1 and consent._record(scope, store, client) == before
    consent._evidence_path(scope, row["evidence"], client=client).write_bytes(b"damaged")
    assert not consent.eligibility(scope, store, client, "email")["allowed"]


def test_internal_evidence_does_not_accept_arbitrary_path_or_other_client(firm):
    scope, store, client = firm
    ref = artifact(firm, "consent", STAFF)
    with pytest.raises(ValueError):
        consent.evidence(scope, ref | {"path": str(scope.data)}, client=client)
    with pytest.raises(ValueError):
        consent.evidence(scope, ref, client="client-b")
    with pytest.raises(ValueError):
        consent.evidence(scope, ref)
    assert artifact(firm, "consent", STAFF) == ref  # exact-byte reuse only


def test_current_active_staff_and_acl_before_grant(firm):
    scope, store, client = firm
    reviewed(firm)
    ref = artifact(firm, "consent", STAFF)
    path = scope.data / "review_users.json"
    accounts = json.loads(path.read_text())
    accounts["users"][STAFF]["active"] = False
    path.write_text(json.dumps(accounts))
    with pytest.raises(PermissionError):
        consent.grant(scope, store, client, "email", actor_email=STAFF, evidence_ref=ref,
                      client_approved_at=(_now() - timedelta(minutes=1)).isoformat(), notice_version="fictional-notice-v1",
                      language="en", source_kind="in_person", approval_description="Fictional actual client approval")
    assert not (store.client_dir(client) / consent.FILE).exists()
    accounts["users"][STAFF]["active"] = True
    path.write_text(json.dumps(accounts))
    import restricted
    (scope.cases / client / restricted.FILE).write_text(json.dumps({"marked": {"on": True}, "people": [], "history": []}))
    with pytest.raises(PermissionError):
        artifact(firm, "consent", STAFF)


def test_provider_cannot_use_pending_credential_until_accepted_result(firm):
    scope, store, client = firm
    granted(firm)
    tokens = []
    def provider(destination, token):
        assert destination == "a@fictional.example"
        tokens.append(token)
        assert not consent.credential_valid(scope, store, entry(store, token))
        return {"status": "sent"}
    result = consent.dispatch(scope, store, client, "email", provider)
    assert result["credential_active"]
    assert consent.credential_valid(scope, store, entry(store, tokens[0]))
    assert not consent.credential_valid(scope, store, {"client": client, "expires": (_now() + timedelta(hours=1)).isoformat()})


@pytest.mark.parametrize("status", ["queued", "dry-run", "failed", "unknown"])
def test_provider_nonacceptance_and_old_outbox_token_are_unusable(firm, status):
    scope, store, client = firm
    granted(firm)
    tokens = []
    def provider(_, token):
        tokens.append(token)
        return {"status": status}
    result = consent.dispatch(scope, store, client, "email", provider)
    assert not result["credential_active"]
    assert not consent.credential_valid(scope, store, entry(store, tokens[0]))


def test_provider_timeout_and_crash_before_result_never_activate(firm):
    scope, store, client = firm
    granted(firm)
    tokens = []
    def timeout(_, token):
        tokens.append(token)
        raise TimeoutError("fictional bounded provider timeout")
    assert consent.dispatch(scope, store, client, "email", timeout)["status"] == "unknown"
    assert not consent.credential_valid(scope, store, entry(store, tokens[0]))
    def crash(_, token):
        tokens.append(token)
        raise SystemExit("fictional process crash seam")
    with pytest.raises(SystemExit):
        consent.dispatch(scope, store, client, "email", crash)
    assert not consent.credential_valid(scope, store, entry(store, tokens[1]))


def test_result_persistence_failure_retains_unusable_token(firm, monkeypatch):
    scope, store, client = firm
    granted(firm)
    tokens = []
    atomic = consent._atomic
    def fail(path, value, **kw):
        if Path(path).parent.name == "communication-attempts" and value.get("state") == "accepted":
            raise OSError("fictional disk failure")
        return atomic(path, value, **kw)
    monkeypatch.setattr(consent, "_atomic", fail)
    def provider(_, token):
        tokens.append(token)
        return {"status": "sent"}
    with pytest.raises(OSError):
        consent.dispatch(scope, store, client, "email", provider)
    assert not consent.credential_valid(scope, store, entry(store, tokens[0]))


def test_revoke_disables_delayed_link_and_session_snapshot(firm):
    scope, store, client = firm
    granted(firm)
    tokens = []
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    old = entry(store, tokens[0])
    assert consent.credential_valid(scope, store, old)
    result = consent.revoke(scope, store, client, ["email"], actor_email=STAFF)
    assert result["state"] == "revoked"
    assert not consent.credential_valid(scope, store, old)
    assert not store._auth()["links"]
    assert consent.dispatch(scope, store, client, "email", lambda *a: pytest.fail("provider after revoke"))["status"] == "held"


def test_revocation_intent_denies_even_cleanup_crash(firm, monkeypatch):
    scope, store, client = firm
    granted(firm)
    tokens = []
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    old = entry(store, tokens[0])
    atomic = consent._atomic
    def fail(path, value, **kw):
        if Path(path).name == "auth.json":
            raise OSError("fictional cleanup crash")
        return atomic(path, value, **kw)
    monkeypatch.setattr(consent, "_atomic", fail)
    with pytest.raises(OSError):
        consent.revoke(scope, store, client, ["email"], actor_email=STAFF)
    assert consent._record(scope, store, client)["revocation_pending"]
    assert not consent.credential_valid(scope, store, old)


def test_dispatch_then_revoke_serializes_provider_acceptance(firm):
    scope, store, client = firm
    granted(firm)
    entered, release, revoked = threading.Event(), threading.Event(), threading.Event()
    tokens, failures = [], []
    def provider(_, token):
        tokens.append(token)
        entered.set()
        assert release.wait(3)
        assert not revoked.is_set()
        return {"status": "sent"}
    def send():
        try:
            consent.dispatch(scope, store, client, "email", provider)
        except BaseException as exc:
            failures.append(exc)
    def stop():
        try:
            consent.revoke(scope, store, client, ["email"], actor_email=STAFF)
            revoked.set()
        except BaseException as exc:
            failures.append(exc)
    sending = threading.Thread(target=send)
    sending.start()
    assert entered.wait(3)
    stopping = threading.Thread(target=stop)
    stopping.start()
    # Verify the same installation lock is owned before releasing the provider.
    import oslock
    assert oslock.held_by_someone(scope.data / "communication.lock")
    release.set()
    sending.join(5)
    stopping.join(5)
    assert not sending.is_alive() and not stopping.is_alive() and not failures and revoked.is_set()
    assert not store._auth()["links"]  # late delivery cannot authenticate


def test_revoke_first_never_calls_provider(firm):
    scope, store, client = firm
    granted(firm)
    consent.revoke(scope, store, client, ["email"], actor_email=STAFF)
    assert consent.dispatch(scope, store, client, "email", lambda *a: pytest.fail("unexpected provider"))["status"] == "held"


@pytest.mark.parametrize("change", ["protected_missing_access", "declined", "missing_case"])
def test_current_case_and_protected_lifecycle_remain_authoritative(firm, change):
    scope, store, client = firm
    granted(firm)
    if change == "protected_missing_access":
        store.update_profile(client, track="vawa")
    elif change == "declined":
        store.update_profile(client, status="declined")
    else:
        shutil.rmtree(scope.cases / client)
    assert consent.dispatch(scope, store, client, "email", lambda *a: pytest.fail("held case dispatched"))["status"] == "held"


def test_explicit_prospect_scope_and_cross_store_duplicate_destination(firm):
    import prospects
    scope, store, client = firm
    granted(firm)
    pscope = consent.Scope(scope.root, scope.data / "portal/prospects", scope.data / "prospects")
    pstore = prospects.store(scope.portal)
    (pscope.cases / "prospect-a").mkdir(parents=True)
    pstore.add_client("prospect-a", "Fictional Prospect", email="a@fictional.example", language="en")
    from portal.contact_access import contact_eligibility
    assert consent._record(scope, store, client)["channels"]["email"]["state"] == "revoked"
    assert contact_eligibility(scope, store, client, "email")["reason"] == "ambiguous_destination"
    assert consent.eligibility(scope, store, client, "email")["reason"] == "actual_client_signoff_required"
    assert store._auth() == {"links": {}, "sessions": {}}
    assert consent.dispatch(scope, store, client, "email", lambda *_: pytest.fail("duplicate contact dispatched"))["status"] == "held"
    assert consent.eligibility(pscope, pstore, "prospect-a", "email")["allowed"] is False
    with pytest.raises(ValueError):
        pscope.check(store, client)
    with pytest.raises(ValueError):
        consent.Scope(scope.root, scope.portal, pscope.cases)


def test_legitimate_prospect_grant_uses_own_case_profile_and_evidence(firm):
    import prospects
    scope, _, _ = firm
    pscope = consent.Scope(scope.root, scope.data / "portal/prospects", scope.data / "prospects")
    pstore = prospects.store(scope.portal)
    (pscope.cases / "prospect-a").mkdir(parents=True)
    pstore.add_client("prospect-a", "Fictional Prospect", email="prospect@fictional.example", language="en")
    pfirm = pscope, pstore, "prospect-a"
    row = granted(pfirm)
    assert row["evidence"]["kind"] == "consent"
    assert consent.eligibility(pscope, pstore, "prospect-a", "email")["allowed"]
    assert not (scope.portal / "clients/prospect-a").exists()
    # The same existing lifecycle guard must prevent a closed prospect resend.
    pstore.update_profile("prospect-a", closed_on="2026-01-01")
    assert not consent.eligibility(pscope, pstore, "prospect-a", "email")["allowed"]


def test_other_installation_mutable_store_override_refused(firm, monkeypatch, tmp_path):
    scope, _, _ = firm
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "foreign/events.jsonl"))
    with pytest.raises(ValueError):
        consent.Scope(scope.root, scope.portal, scope.cases)


def test_missing_ledger_effect_is_visible_and_history_is_authoritative(firm, monkeypatch):
    import events
    scope, store, client = firm
    monkeypatch.setattr(events, "record", lambda *a, **kw: None)
    row = granted(firm)
    assert row["audit_status"] == "unavailable"
    assert consent._record(scope, store, client)["history"][-1]["revision"] == row["revision"]
    assert consent.eligibility(scope, store, client, "email")["allowed"]


def test_language_and_notice_edits_reopen_existing_review(firm):
    scope, store, client = firm
    granted(firm)
    assert wording.readiness(scope, "en")["ready"]
    path = scope.root / "src/portal/static/portal.html"
    path.write_bytes(path.read_bytes() + b"\n<!-- fictional wording edit -->")
    assert not wording.readiness(scope, "en")["ready"]
    assert not consent.eligibility(scope, store, client, "email")["allowed"]


def test_generic_approved_status_and_maintenance_date_are_not_review(firm):
    scope, _, _ = firm
    current = wording.bundle(scope.root, "ht")
    (scope.data / "rules_approved.json").write_text(json.dumps({wording.practice_id("ht"):
        [{"hash": current["digest"], "by": "Generic reviewer", "role": "paralegal"}]}))
    (scope.data / "maintenance_log.json").write_text(json.dumps({"client_wording": {"last_checked": "2026-10-05", "log": []}}))
    assert not wording.readiness(scope, "ht")["ready"]
    ref = artifact(firm)
    wording.review_attorney(scope, "ht", actor_email=ATTORNEY, evidence_ref=ref)
    assert wording.readiness(scope, "ht")["reason"] == "qualified_translation_review_required"
    wording.review_translation(scope, "ht", actor_email=ATTORNEY, reviewer_name="Fictional qualified speaker",
                               qualification="Fictional qualified review performed", evidence_ref=ref)
    assert wording.readiness(scope, "ht")["ready"]


def test_attorney_authority_and_evidence_are_current(firm):
    scope, _, _ = firm
    ref = reviewed(firm)
    with pytest.raises(PermissionError):
        wording.review_attorney(scope, "en", actor_email=STAFF, evidence_ref=ref)
    path = scope.data / "review_users.json"
    data = json.loads(path.read_text())
    data["users"][ATTORNEY]["active"] = False
    path.write_text(json.dumps(data))
    assert not wording.readiness(scope, "en")["ready"]
    data["users"][ATTORNEY]["active"] = True
    path.write_text(json.dumps(data))
    consent._evidence_path(scope, ref).write_bytes(b"changed")
    assert not wording.readiness(scope, "en")["ready"]


def test_catalog_cache_key_tracks_exact_bundle_changes(firm, monkeypatch):
    scope, _, _ = firm
    # The real generic catalog uses its own product root. Bind that default in
    # this fictional probe, without changing any shipped source or firm file.
    entries = wording.practice_entries
    stamp = wording.stamp
    monkeypatch.setattr(wording, "practice_entries", lambda root=scope.root: entries(root))
    monkeypatch.setattr(wording, "stamp", lambda root=scope.root: stamp(root))
    first = next(e for e in approval.catalog() if e["id"] == wording.practice_id("en"))
    path = scope.root / "src/portal/notify.py"
    path.write_bytes(path.read_bytes() + b"\n# fictional notice wording change\n")
    second = next(e for e in approval.catalog() if e["id"] == first["id"])
    assert first["hash"] != second["hash"]
    with pytest.raises(ValueError, match="review evidence"):
        approval.approve(second["id"], "Generic reviewer", role="attorney")
