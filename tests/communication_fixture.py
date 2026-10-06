"""Explicit fictional installation/reviews/client approval for caller tests only.

No production autoapproval, provider request, imported grant or raw-token bypass.
Callers choose which fictional client actually approved service communication.
"""
from datetime import timedelta
import json
from pathlib import Path
import shutil

import client_language_readiness as wording
from portal import communication_consent as consent
from portal.store import _now

ATTORNEY = "attorney@fictional.example"
STAFF = "staff@fictional.example"
REPO = Path(__file__).resolve().parents[1]


def installation(root, monkeypatch):
    root = Path(root)
    for name in wording.SOURCES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
    data = root / "data"
    data.mkdir(exist_ok=True)
    for key, name in {"I485_RULES_APPROVED": "rules_approved.json", "I485_MAINTENANCE_LOG": "maintenance_log.json",
                      "I485_EVENTS": "events.jsonl", "I485_SETTINGS": "settings.json", "I485_JOBS": "jobs",
                      "I485_CASES": "clients", "I485_PROSPECTS": "prospects", "PORTAL_DATA": "portal",
                      "I485_INDEX": "index.db", "I485_QUERY_DB": "query.db", "I485_INBOX": "inbox",
                      "I485_READER_EXAMPLES": "reader_examples.jsonl"}.items():
        monkeypatch.setenv(key, str(data / name))
    import settings
    monkeypatch.setattr(settings, "PATH", data / "settings.json")
    monkeypatch.setattr(wording, "ROOT", root)
    (data / "communication_notice.json").write_text(json.dumps({"schema_version": 1, "version": "fictional-caller-notice-v1",
        "texts": {lang: "FICTIONAL CALLER TEST NOTICE: " + lang for lang in wording.LANGUAGES}}), encoding="utf-8")
    (data / "review_users.json").write_text(json.dumps({"users": {
        ATTORNEY: {"name": "Fictional Attorney", "role": "attorney", "active": True},
        STAFF: {"name": "Fictional Staff", "role": "paralegal", "active": True}}, "sessions": {}}), encoding="utf-8")
    (data / "clients").mkdir(exist_ok=True)
    return data


def approve_client(store, client, *, channel="email"):
    scope = store.communication_scope()
    # Legitimate portal-only scaffold, never fabricate processing/meta evidence.
    (scope.cases / client).mkdir(parents=True, exist_ok=True)
    language = store.profile(client)["language"]
    ref = consent.retain_evidence(scope, b'{"fictional":true,"review":"Actual fictional wording review"}',
                                  actor_email=ATTORNEY, kind="wording")
    wording.review_attorney(scope, language, actor_email=ATTORNEY, evidence_ref=ref)
    if language != "en":
        wording.review_translation(scope, language, actor_email=ATTORNEY, reviewer_name="Fictional Qualified Reviewer",
                                   qualification="FICTIONAL actual qualified review evidence", evidence_ref=ref)
    ref = consent.retain_evidence(scope, b'{"fictional":true,"signoff":"Actual fictional in-person service approval"}',
                                  actor_email=STAFF, kind="consent", store=store, client=client)
    return consent.grant(scope, store, client, channel, actor_email=STAFF, evidence_ref=ref,
                         client_approved_at=(_now() - timedelta(minutes=1)).isoformat(),
                         notice_version="fictional-caller-notice-v1", language=language, source_kind="in_person",
                         approval_description="FICTIONAL TEST: this client actually approved this service channel in person.")


def accepted_link(store, client):
    tokens = []
    result = consent.dispatch(store.communication_scope(), store, client, "email",
                              lambda _, token: tokens.append(token) or {"status": "sent"})
    assert result["status"] == "sent" and result["credential_active"], result
    return tokens[0]


def review_request(store, client, request, *, mode="as_written"):
    from portal.request_readiness import review_request as record_review
    scope = store.communication_scope()
    ref = consent.retain_evidence(scope, b'{"fictional":true,"review":"Exact fictional request English wording"}',
                                  actor_email=ATTORNEY, kind="request_wording", store=store, client=client)
    return record_review(scope, store, client, request["id"], actor_email=ATTORNEY,
                         evidence_ref=ref, mode=mode, publish=True,
                         reviewer_name="Fictional Qualified Reviewer" if mode == "translated" else None,
                         qualification="FICTIONAL actual qualified review of this complete request" if mode == "translated" else None)
