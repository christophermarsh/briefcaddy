"""Shared actual history/settings writers serialize with current dispatch."""
import json
import threading

import pytest

import client_language_readiness as wording
import maintenance
import settings
from portal import communication_consent as consent, queue_bridge
from rules import approval
from test_communication_consent import firm, granted, artifact, STAFF, ATTORNEY  # noqa: F401 -- pytest fixture registration and helper reexports


def start(fn, failures):
    def work():
        try:
            fn()
        except BaseException as exc:
            failures.append(exc)
    thread = threading.Thread(target=work)
    thread.start()
    return thread


def test_firm_settings_mutation_waits_for_actual_provider_gate_then_reopens_readiness(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    entered, release, changed = threading.Event(), threading.Event(), threading.Event()
    failures, results = [], []
    def provider(*_):
        entered.set()
        assert release.wait(5)
        assert not changed.is_set() and not settings.PATH.exists()
        return {"status": "sent"}
    sending = start(lambda: results.append(consent.dispatch(scope, store, client, "email", provider)), failures)
    assert entered.wait(5)
    mutation = start(lambda: (settings.add_office("Fictional Attorney"), changed.set()), failures)
    assert not changed.wait(0.1)
    release.set()
    sending.join(5)
    mutation.join(5)
    assert not sending.is_alive() and not mutation.is_alive() and not failures
    assert results[0]["status"] == "sent" and changed.is_set()
    assert not wording.readiness(scope, "en")["ready"]
    assert not consent.eligibility(scope, store, client, "email")["allowed"]


def test_generic_upkeep_and_actual_translation_attestation_preserve_both_rows(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    import deployment
    scope, _, _ = firm
    ref = artifact(firm)
    registry = scope.root / "fictional-maintenance.json"
    registry.write_text(json.dumps({"items": [{"id": "client_wording", "what": "Fictional wording upkeep", "party": "firm"}]}))
    monkeypatch.setattr(deployment, "responsible", lambda _: "firm")
    entered, release, checked = threading.Event(), threading.Event(), threading.Event()
    failures = []
    atomic = wording._atomic
    def hold(path, value, **kw):
        if path.name == "maintenance_log.json":
            entered.set()
            assert release.wait(5)
        return atomic(path, value, **kw)
    monkeypatch.setattr(wording, "_atomic", hold)
    reviewed = start(lambda: wording.review_translation(scope, "pt", actor_email=ATTORNEY,
                      reviewer_name="Fictional Qualified Reviewer", qualification="Fictional actual reviewed qualification", evidence_ref=ref), failures)
    assert entered.wait(5)
    generic = start(lambda: (maintenance.mark("client_wording", "Fictional Staff", path=registry,
                                             log_path=scope.data / "maintenance_log.json"), checked.set()), failures)
    assert not checked.wait(0.1)
    release.set()
    reviewed.join(5)
    generic.join(5)
    assert not reviewed.is_alive() and not generic.is_alive() and not failures
    rows = json.loads((scope.data / "maintenance_log.json").read_text())["client_wording"]["log"]
    assert len(rows) == 2 and rows[0]["review_type"] == "actual_qualified_translation_review" and rows[1]["by"] == "Fictional Staff"


def test_generic_rule_approval_and_actual_wording_review_preserve_both_histories(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, _, _ = firm
    ref = artifact(firm)
    rule = next(row for row in approval.catalog() if not row.get("requires_review_evidence"))
    entered, release, approved = threading.Event(), threading.Event(), threading.Event()
    failures = []
    atomic = queue_bridge._atomic
    def hold(path, value, **kw):
        if path.name == "rules_approved.json" and wording.practice_id("en") in value and rule["id"] not in value:
            entered.set()
            assert release.wait(5)
        return atomic(path, value, **kw)
    monkeypatch.setattr(queue_bridge, "_atomic", hold)
    reviewed = start(lambda: wording.review_attorney(scope, "en", actor_email=ATTORNEY, evidence_ref=ref), failures)
    assert entered.wait(5)
    generic = start(lambda: (approval.approve(rule["id"], "Fictional Attorney", "attorney"), approved.set()), failures)
    assert not approved.wait(0.1)
    release.set()
    reviewed.join(5)
    generic.join(5)
    assert not reviewed.is_alive() and not generic.is_alive() and not failures
    log = json.loads((scope.data / "rules_approved.json").read_text())
    assert len(log[rule["id"]]) == 1 and len(log[wording.practice_id("en")]) == 1


def test_actual_firm_document_override_bytes_reopen_review_and_foreign_data_writer_refuses(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, _, _ = firm
    granted(firm)
    prior = wording.bundle(scope.root, "en")
    (scope.data / "firm_documents.json").write_text(json.dumps({"version": 1, "fictional": "changed actual office wording"}))
    assert wording.bundle(scope.root, "en")["digest"] != prior["digest"] and not wording.readiness(scope, "en")["ready"]
    foreign = scope.root.parent / "foreign/data/settings.json"
    monkeypatch.setattr(settings, "PATH", foreign)
    with pytest.raises(ValueError, match="another installation"):
        settings.add_office("Fictional Staff")
    assert not foreign.exists()


def test_idle_timeout_and_administrative_history_do_not_change_effective_client_wording(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    prior = wording.bundle(scope.root, "en")["digest"]
    settings.save("sign_in", {"idle_minutes": 45}, "Fictional Attorney")
    assert wording.bundle(scope.root, "en")["digest"] == prior
    settings.save("sign_in", {"idle_minutes": 60}, "Fictional Attorney")
    assert wording.bundle(scope.root, "en")["digest"] == prior and consent.eligibility(scope, store, client, "email")["allowed"]
    settings.save("firm", {"firm.business_name": "Fictional Actual New Firm Name"}, "Fictional Attorney")
    assert wording.bundle(scope.root, "en")["digest"] != prior and not consent.eligibility(scope, store, client, "email")["allowed"]


@pytest.mark.parametrize("name,section,key", [("schemas/packets/companion_forms.json", "firm", "firm.street"), ("schemas/firm/firm_profile.json", "facts", "firm.business_name"), ("schemas/cover_letters/i485.json", "letterhead", "tagline")])
def test_actual_rendered_default_assets_reopen_wording_review(firm, name, section, key):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    prior = wording.bundle(scope.root, "en")["digest"]
    path = scope.root / name
    content = json.loads(path.read_text())
    content[section][key] = "Fictional changed rendered default"
    path.write_text(json.dumps(content))
    assert wording.bundle(scope.root, "en")["digest"] != prior
    assert not wording.readiness(scope, "en")["ready"]
    assert not consent.eligibility(scope, store, client, "email")["allowed"]
