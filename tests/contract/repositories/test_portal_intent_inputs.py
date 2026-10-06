"""Input/profile checks only: these do not validate a PostgreSQL transaction."""
from dataclasses import replace
import json
from uuid import UUID

import pytest

from law_app.bootstrap.fictional_postgres import FictionalPostgresProfile, build_fictional_portal_intents
from law_app.ports.portal_intents import FirmEnrollment, PortalIntentRequest, RevisionReference, canonical_request


@pytest.fixture
def intent_request():
    return PortalIntentRequest(UUID(int=1), "a" * 32, "b" * 32, UUID(int=2),
                               (RevisionReference(UUID(int=3), "c" * 64, 5),),
                               "fictional-v1", {"language": "Español", "ordered": [1, 2]})


def test_digest_freezes_unicode_json_and_ordered_exact_inputs(intent_request):
    payload, digest, manifest = canonical_request(intent_request)
    assert "Español" in payload and " " not in payload
    assert canonical_request(replace(intent_request, options={"ordered": [1, 2], "language": "Español"})) == (payload, digest, manifest)
    changed, changed_digest, same_manifest = canonical_request(replace(intent_request, options={"ordered": [2, 1]}))
    assert changed_digest != digest and same_manifest == manifest
    assert json.loads(payload)["options"] == {"language": "Español", "ordered": [1, 2]}
    intent_request.options["ordered"].append(3)
    assert json.loads(payload)["options"]["ordered"] == [1, 2]
    _, new_digest, new_manifest = canonical_request(replace(intent_request, revisions=(RevisionReference(UUID(int=3), "d" * 64, 5),)))
    assert new_digest != digest and new_manifest != manifest


@pytest.mark.parametrize("change", [
    {"options": {"number": float("nan")}}, {"options": {1: "not a JSON key"}},
    {"operation_id": "../foreign"}, {"revisions": ()},
    {"revisions": (RevisionReference(UUID(int=3), "c" * 64, True),)},
    {"revisions": (RevisionReference(UUID(int=3), "c" * 64, 5),) * 2},
])
def test_invalid_intent_input_is_rejected_before_connection(intent_request, change):
    from law_app.adapters.persistence.postgres.portal_intents import PostgresPortalIntents
    repository = PostgresPortalIntents(FirmEnrollment(UUID(int=4), UUID(int=5)),
                                      lambda: pytest.fail("Invalid input reached connection"))
    with pytest.raises(ValueError):
        repository.commit_portal_intent(replace(intent_request, **change))


def test_fictional_profile_is_disabled_and_consumes_local_endpoint_only_when_used():
    scope = FirmEnrollment(UUID(int=4), UUID(int=5))
    called = []
    def factory(**options):
        called.append(options)
        raise RuntimeError("stop before database")
    assert build_fictional_portal_intents(FictionalPostgresProfile(), scope, connection_factory=factory) is None
    profile = FictionalPostgresProfile(True, True, database="law_app_fictional_" + "a" * 32)
    repository = build_fictional_portal_intents(profile, scope, connection_factory=factory)
    assert called == []
    with pytest.raises(RuntimeError):
        repository._connect()
    assert called == [{"host": "127.0.0.1", "database": profile.database}]
    for invalid in (replace(profile, disposable=False), replace(profile, host="remote"), replace(profile, database="production")):
        with pytest.raises(ValueError):
            build_fictional_portal_intents(invalid, scope, connection_factory=factory)

@pytest.mark.parametrize("seconds", [True, -1, 0, 2**31, 1.5])
def test_synthetic_lease_duration_rejects_invalid_input(seconds):
    from law_app.ports.synthetic_work import duration
    with pytest.raises(ValueError):
        duration(seconds)


def test_synthetic_tokens_cannot_cross_scope_before_io():
    from datetime import datetime, timezone
    from law_app.adapters.persistence.postgres.synthetic_work import PostgresSyntheticWork
    from law_app.ports.synthetic_work import ClaimRejected, WorkClaim
    scope = FirmEnrollment(UUID(int=4), UUID(int=5))
    claim = WorkClaim(FirmEnrollment(UUID(int=6), UUID(int=5)), *(UUID(int=n) for n in range(7, 11)),
                      1, 1, datetime.now(timezone.utc))
    work = PostgresSyntheticWork(scope, lambda: pytest.fail("Cross-scope token reached database"))
    with pytest.raises(ClaimRejected):
        work.renew(claim, 30)


def test_synthetic_handler_uses_updated_tokens_and_retains_human_review():
    from law_app.ports.synthetic_work import run_synthetic_once
    calls = []
    class FakeWork:
        def claim(self, job, owner, seconds):
            calls.append('claim')
            return 1
        def renew(self, token, seconds):
            assert token == 1
            calls.append('renew')
            return 2
        def progress(self, token, value):
            assert token == 2
            calls.append('progress')
            return 3
        def complete(self, token, value):
            assert token == 3 and value['synthetic'] and value['review_required']
            calls.append('complete')
            return value
    value = run_synthetic_once(FakeWork(), UUID(int=1), UUID(int=2), lease_seconds=30)
    assert value['job_id'] == str(UUID(int=1))
    assert calls == ['claim', 'renew', 'progress', 'complete']


@pytest.fixture
def original_store(tmp_path):
    from law_app.adapters.storage.filesystem.originals import FilesystemOriginals
    scope = FirmEnrollment(UUID(int=4), UUID(int=5))
    return FilesystemOriginals(tmp_path, scope, disposable=True)


def test_actual_original_bytes_reuse_and_immutable_conflict(original_store):
    from law_app.ports.originals import OriginalConflict
    data = b'fictional original\x00\xff'
    first = original_store.publish(UUID(int=1), UUID(int=2), data)
    assert original_store.verified_read(first) == data and first.byte_length == len(data)
    assert original_store.publish(UUID(int=1), UUID(int=2), data) == first
    with pytest.raises(OriginalConflict):
        original_store.publish(UUID(int=1), UUID(int=2), b'changed')
    assert original_store.verified_read(first) == data


@pytest.mark.parametrize('change', ['scope', 'locator', 'identity'])
def test_original_scope_and_path_refusal(original_store, change):
    from law_app.ports.originals import OriginalUnavailable
    original = original_store.publish(UUID(int=1), UUID(int=2), b'fictional')
    altered = {'scope': {'scope': FirmEnrollment(UUID(int=9), UUID(int=5))},
               'locator': {'locator': '../outside'}, 'identity': {'revision_id': '../outside'}}[change]
    with pytest.raises((OriginalUnavailable, ValueError)):
        original_store.verified_read(replace(original, **altered))


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'hardlink'])
def test_original_missing_corrupt_or_linked_bytes_refused(original_store, damage):
    import os
    from law_app.ports.originals import OriginalUnavailable
    original = original_store.publish(UUID(int=1), UUID(int=2), b'fictional')
    path = original_store._path(original.intake_id, original.revision_id)
    if damage == 'missing':
        path.unlink()
    elif damage == 'corrupt':
        path.write_bytes(b'corrupted')
    else:
        os.link(path, path.parent / 'second-link')
    with pytest.raises(OriginalUnavailable):
        original_store.verified_read(original)


def test_original_publication_interruption_preserves_unpublished_residue(original_store, monkeypatch):
    from law_app.adapters.storage.filesystem import originals
    def fail(*args):
        raise OSError('fictional interrupted publication')
    monkeypatch.setattr(originals.os, 'link', fail)
    with pytest.raises(OSError):
        original_store.publish(UUID(int=1), UUID(int=2), b'fictional')
    path = original_store._path(UUID(int=1), UUID(int=2))
    assert not path.exists()
    assert [p.read_bytes() for p in path.parent.glob('staging-*')] == [b'fictional']


def test_original_observed_reparse_point_refused(original_store, monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from law_app.ports.originals import OriginalUnavailable
    from law_app.adapters.storage.filesystem.originals import FilesystemOriginals
    # Native hardlinks above exercise the real filesystem; simulate Windows's
    # junction attribute without creating a host link/altering privileges.
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(
        st_mode=0o40700, st_file_attributes=0x400, st_nlink=1))
    with pytest.raises(OriginalUnavailable):
        FilesystemOriginals._safe(original_store.root, directory=True)


def test_original_bound_checked_before_publication(original_store):
    original_store.MAX_BYTES = 2
    with pytest.raises(ValueError):
        original_store.publish(UUID(int=1), UUID(int=2), b'too big')
    assert list(original_store.root.iterdir()) == []


def test_fictional_delivery_invokes_local_handler_between_claim_and_ack():
    from law_app.adapters.persistence.postgres.outbox_delivery import deliver_fictional_once
    calls = []
    token = object()
    class Delivery:
        def claim(self, *args):
            calls.append('committed_claim')
            return token
        def acknowledge(self, value):
            assert value is token
            calls.append('ack')
    def handle(value):
        assert value is token
        calls.append('local_handler')
        return 'accepted'
    assert deliver_fictional_once(Delivery(), UUID(int=1), UUID(int=2), handle) == 'accepted'
    assert calls == ['committed_claim', 'local_handler', 'ack']


def test_fictional_delivery_handler_failure_does_not_acknowledge():
    from law_app.adapters.persistence.postgres.outbox_delivery import deliver_fictional_once
    class Delivery:
        def claim(self, *args):
            return object()
        def acknowledge(self, value):
            pytest.fail('Failed handler acknowledged')
    def fail(value):
        raise RuntimeError('fictional handler failure')
    with pytest.raises(RuntimeError):
        deliver_fictional_once(Delivery(), UUID(int=1), UUID(int=2), fail)


def test_fictional_service_worker_roles_refuse_wrong_entrypoint_before_io():
    from types import SimpleNamespace
    from law_app.application.intake.fictional_service import FictionalActor, FictionalIntakeService
    from law_app.interfaces.worker.fictional import command
    from law_app.ports.synthetic_work import ClaimRejected
    scope = FirmEnrollment(UUID(int=4), UUID(int=5))
    worker = FictionalActor(scope, UUID(int=6), 'worker')
    service = FictionalIntakeService(worker, SimpleNamespace(work=SimpleNamespace(scope=scope)))
    with pytest.raises(ClaimRejected):
        service.status(UUID(int=7))
    with pytest.raises(ClaimRejected):
        command(SimpleNamespace(actor=replace(worker, role='service')), 'once', {'outbox_id': str(UUID(int=8))})


def test_fictional_command_cannot_override_bound_actor_or_scope():
    from law_app.interfaces.fictional_cli import command
    with pytest.raises(ValueError):
        command(None, 'status', {'job_id': str(UUID(int=1)), 'firm_id': str(UUID(int=9))})


def test_fictional_runtime_requires_explicit_opt_in_before_root_or_driver(tmp_path):
    from law_app.bootstrap.fictional_runtime import compose_fictional_runtime
    config = {'enabled': False, 'disposable': True, 'host': '127.0.0.1', 'database': 'law_app_fictional_'+ 'a'*32,
        'port': 54321, 'originals_root': str(tmp_path/'missing'), 'firm_id': str(UUID(int=1)),
        'enrollment_id': str(UUID(int=2)), 'actor_id': str(UUID(int=3)), 'role': 'service', 'pending_work_cap': 2}
    with pytest.raises(ValueError, match='Explicit disposable'):
        compose_fictional_runtime(config, 'x'*43)
    assert not (tmp_path/'missing').exists()


def test_bounded_delivery_scan_skips_known_rejection_and_contention(monkeypatch):
    from law_app.adapters.persistence.postgres.outbox_delivery import PostgresOutboxDelivery
    from law_app.ports.synthetic_work import ClaimRejected
    delivery = PostgresOutboxDelivery(FirmEnrollment(UUID(int=4), UUID(int=5)), lambda: pytest.fail('Unexpected IO'))
    ids, called, token = tuple(UUID(int=n) for n in range(1,4)), [], object()
    monkeypatch.setattr(delivery, 'pending_ids', lambda *, scan_limit: ids[:scan_limit])
    def claim(identity, owner, seconds):
        called.append(identity)
        if identity == ids[0]:
            raise ClaimRejected('Current eligibility refused')
        return None if identity == ids[1] else token
    monkeypatch.setattr(delivery, 'claim', claim)
    assert delivery.claim_next(UUID(int=6), 30, scan_limit=2) is None
    assert called == list(ids[:2])
    called.clear()
    assert delivery.claim_next(UUID(int=6), 30, scan_limit=3) is token
    assert called == list(ids)


def test_bounded_delivery_scan_propagates_unknown_outcome(monkeypatch):
    from law_app.adapters.persistence.postgres.outbox_delivery import PostgresOutboxDelivery
    from law_app.ports.synthetic_work import WorkPersistenceFailure
    delivery = PostgresOutboxDelivery(FirmEnrollment(UUID(int=4), UUID(int=5)), lambda: pytest.fail('Unexpected IO'))
    monkeypatch.setattr(delivery, 'pending_ids', lambda *, scan_limit: (UUID(int=1), UUID(int=2)))
    called = []
    def unknown(identity, owner, seconds):
        called.append(identity)
        raise WorkPersistenceFailure('Unknown commit')
    monkeypatch.setattr(delivery, 'claim', unknown)
    with pytest.raises(WorkPersistenceFailure):
        delivery.claim_next(UUID(int=6), 30)
    assert called == [UUID(int=1)]


@pytest.mark.parametrize('limit', [True, 0, 17])
def test_delivery_scan_bound_rejected_before_io(limit):
    from law_app.adapters.persistence.postgres.outbox_delivery import PostgresOutboxDelivery
    delivery = PostgresOutboxDelivery(FirmEnrollment(UUID(int=4), UUID(int=5)), lambda: pytest.fail('Unexpected IO'))
    with pytest.raises(ValueError):
        delivery.pending_ids(scan_limit=limit)


def test_admission_canonical_input_binds_actor_purpose_bytes_and_expected_generation():
    from law_app.application.intake.fictional_service import FictionalActor
    from law_app.ports.fictional_admissions import AdmissionRequest, canonical_admission
    actor = FictionalActor(FirmEnrollment(UUID(int=1),UUID(int=2)),UUID(int=3),'service')
    request = AdmissionRequest(UUID(int=4),UUID(int=5),'a'*32,'b'*32,None)
    payload,digest,manifest,revision = canonical_admission(actor,request,b'fictional')
    assert canonical_admission(actor,request,b'fictional') == (payload,digest,manifest,revision)
    assert json.loads(payload)['purpose'] == 'fictional_intake_upload'
    assert 'authority_evidence_id' not in json.loads(payload)
    for other_actor,other_request,data in ((replace(actor,actor_id=UUID(int=9)),request,b'fictional'),
            (actor,replace(request,expected_current_generation='c'*32),b'fictional'),
            (actor,request,b'changed')):
        assert canonical_admission(other_actor,other_request,data)[1] != digest


@pytest.mark.parametrize('expected', ['../foreign','a'*32])
def test_admission_rejects_invalid_or_same_expected_generation(expected):
    from law_app.application.intake.fictional_service import FictionalActor
    from law_app.ports.fictional_admissions import AdmissionRequest, canonical_admission
    actor = FictionalActor(FirmEnrollment(UUID(int=1),UUID(int=2)),UUID(int=3),'service')
    with pytest.raises(ValueError):
        canonical_admission(actor,AdmissionRequest(UUID(int=4),UUID(int=5),'a'*32,'b'*32,expected),b'fictional')


def test_upload_command_refuses_caller_authority_evidence():
    from law_app.interfaces.fictional_cli import command
    with pytest.raises(ValueError):
        command(None,'upload',{'intake_id':str(UUID(int=1)),'revision_id':str(UUID(int=2)),
            'data_b64':'ZmljdGlvbmFs','generation_id':'a'*32,'operation_id':'b'*32,
            'authority_evidence_id':str(UUID(int=3))})


def test_recovery_commands_cannot_supply_actor_scope_or_authority():
    from law_app.interfaces.fictional_cli import command
    request={'intake_id':str(UUID(int=1)),'revision_id':str(UUID(int=2)),
        'data_b64':'ZmljdGlvbmFs','generation_id':'a'*32,'operation_id':'b'*32,'expected_current_generation':None}
    for name in ('admission-status','abandon'):
        for key in ('actor_id','firm_id','authority_evidence_id'):
            with pytest.raises(ValueError):
                command(None,name,request | {key:str(UUID(int=3))})


def test_worker_actor_cannot_read_or_abandon_admission_history():
    from types import SimpleNamespace
    from law_app.application.intake.fictional_service import FictionalActor,FictionalIntakeService
    from law_app.ports.synthetic_work import ClaimRejected
    scope=FirmEnrollment(UUID(int=1),UUID(int=2))
    service=FictionalIntakeService(FictionalActor(scope,UUID(int=3),'worker'),
        SimpleNamespace(work=SimpleNamespace(scope=scope)))
    for method in (service.admission_status,service.abandon):
        with pytest.raises(ClaimRejected):
            method(None,b'fictional')
