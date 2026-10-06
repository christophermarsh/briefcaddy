"""Real PostgreSQL acceptance; requires the explicit disposable-runner fixture.

Ordinary collection skips these; it never discovers DSNs or opens a database.
"""
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from law_app.bootstrap.fictional_postgres import FictionalPostgresProfile, build_fictional_portal_intents
from law_app.ports.portal_intents import (
    FirmEnrollment, IntentConflict, IntentPersistenceFailure, IntentUnavailable,
    PortalIntentRequest, RevisionReference, canonical_request,
)


@pytest.fixture
def fictional_database(request):
    fixture = getattr(request.config, "fictional_postgres_fixture", None)
    if fixture is None:
        pytest.skip("Requires explicitly authorized disposable local PostgreSQL runner")
    return fixture


@pytest.fixture
def make_world(fictional_database):
    def make():
        scope = FirmEnrollment(uuid4(), uuid4())
        intake, authority, revision = uuid4(), uuid4(), uuid4()
        intent = PortalIntentRequest(intake, uuid4().hex, uuid4().hex, authority,
                                     (RevisionReference(revision, "a" * 64, 9),),
                                     "fictional-v1", {"answers_revision": str(revision)})
        _, _, manifest = canonical_request(intent)
        profile = FictionalPostgresProfile(True, True, database=fictional_database.database)
        connect = lambda: fictional_database.connection_factory(host=profile.host, database=profile.database)
        with connect() as conn, conn.transaction():
            conn.execute("INSERT INTO law_app.enrollments VALUES (%s,%s,true)", (scope.firm_id, scope.enrollment_id))
            conn.execute("INSERT INTO law_app.intakes VALUES (%s,%s,%s,true)", (scope.firm_id, scope.enrollment_id, intake))
            conn.execute("INSERT INTO law_app.portal_generations VALUES (%s,%s,%s,%s,'ready',%s,%s)",
                         (scope.firm_id, scope.enrollment_id, intake, intent.generation_id, authority, manifest))
            conn.execute("INSERT INTO law_app.revisions VALUES (%s,%s,%s,%s,%s,%s,%s,'readable')",
                         (scope.firm_id, scope.enrollment_id, intake, revision, "a" * 64, 9, "fictional:original:" + revision.hex))
            conn.execute("INSERT INTO law_app.generation_revisions VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (scope.firm_id, scope.enrollment_id, intake, intent.generation_id, revision, "a" * 64, 9))
        repository = build_fictional_portal_intents(profile, scope, connection_factory=fictional_database.connection_factory)
        return repository, intent, connect, scope
    return make


def counts(connect, scope):
    with connect() as conn:
        return tuple(conn.execute(f"SELECT count(*) FROM law_app.{table} WHERE firm_id=%s AND enrollment_id=%s",
                                  (scope.firm_id, scope.enrollment_id)).fetchone()[0]
                     for table in ("job_intents", "processing_attempts", "job_revisions", "outbox_intents"))


def test_duplicate_reuses_one_committed_attempt_job_and_outbox(make_world):
    repository, intent, connect, scope = make_world()
    first = repository.commit_portal_intent(intent)
    second = repository.commit_portal_intent(intent)
    assert not first.reused and second.reused
    assert (first.job_id, first.attempt_id, first.outbox_id) == (second.job_id, second.attempt_id, second.outbox_id)
    assert counts(connect, scope) == (1, 1, 1, 1)


def test_changed_payload_conflicts_without_replacement(make_world):
    repository, intent, connect, scope = make_world()
    first = repository.commit_portal_intent(intent)
    with pytest.raises(IntentConflict):
        repository.commit_portal_intent(replace(intent, options={"changed": True}))
    assert counts(connect, scope) == (1, 1, 1, 1)
    assert repository.commit_portal_intent(intent).job_id == first.job_id


def test_outbox_insert_failure_rolls_back_entire_new_aggregate(make_world, monkeypatch):
    from law_app.adapters.persistence.postgres import portal_intents
    repository, intent, connect, scope = make_world()
    existing = repository.commit_portal_intent(intent)
    failed_job, failed_attempt = uuid4(), uuid4()
    ids = iter((failed_job, failed_attempt, existing.outbox_id))
    # Genuine PostgreSQL unique violation at the last insert, after job, attempt
    # and revision-link writes; no fake transaction or SQLite substitution.
    monkeypatch.setattr(portal_intents, "uuid4", lambda: next(ids))
    with pytest.raises(IntentPersistenceFailure):
        repository.commit_portal_intent(replace(intent, operation_id=uuid4().hex))
    assert counts(connect, scope) == (1, 1, 1, 1)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM law_app.job_intents WHERE job_id=%s", (failed_job,)).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM law_app.processing_attempts WHERE attempt_id=%s", (failed_attempt,)).fetchone() == (0,)


@pytest.mark.parametrize("which", ["firm", "enrollment"])
def test_foreign_scope_cannot_publish_existing_generation(make_world, which):
    from law_app.adapters.persistence.postgres.portal_intents import PostgresPortalIntents
    repository, intent, connect, scope = make_world()
    foreign = replace(scope, **{("firm_id" if which == "firm" else "enrollment_id"): uuid4()})
    with pytest.raises(IntentUnavailable):
        PostgresPortalIntents(foreign, connect).commit_portal_intent(intent)
    assert counts(connect, scope) == (0, 0, 0, 0)
    assert counts(connect, foreign) == (0, 0, 0, 0)


def test_foreign_revision_rejected_by_repository_and_composite_foreign_key(make_world):
    import psycopg
    repository, intent, connect, scope = make_world()
    _, other, _, _ = make_world()
    poisoned = replace(intent, revisions=other.revisions)
    _, _, manifest = canonical_request(poisoned)
    with connect() as conn:
        conn.execute("UPDATE law_app.portal_generations SET revision_manifest_sha256=%s WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s AND generation_id=%s",
                     (manifest, scope.firm_id, scope.enrollment_id, intent.intake_id, intent.generation_id))
    with pytest.raises(IntentUnavailable):
        repository.commit_portal_intent(poisoned)
    assert counts(connect, scope) == (0, 0, 0, 0)
    ref = other.revisions[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connect() as conn, conn.transaction():
        conn.execute("INSERT INTO law_app.generation_revisions VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (scope.firm_id, scope.enrollment_id, intent.intake_id, intent.generation_id, ref.revision_id, ref.sha256, ref.byte_length))


def test_racing_same_key_submissions_commit_one_aggregate(make_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    repository, intent, connect, scope = make_world()
    barrier = Barrier(2)
    def submit():
        barrier.wait(timeout=10)
        return repository.commit_portal_intent(intent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        receipts = [future.result(timeout=10) for future in futures]
    assert sum(not value.reused for value in receipts) == 1
    assert {(r.job_id, r.attempt_id, r.outbox_id) for r in receipts} == {
        (receipts[0].job_id, receipts[0].attempt_id, receipts[0].outbox_id)}
    assert counts(connect, scope) == (1, 1, 1, 1)


def test_lost_commit_response_reconciles_and_denial_keeps_outcome_unknown(make_world):
    from contextlib import contextmanager
    from law_app.adapters.persistence.postgres.portal_intents import PostgresPortalIntents
    repository, intent, connect, scope = make_world()
    @contextmanager
    def lost_response():
        with connect() as conn:
            yield conn
        raise ConnectionError('Synthetic lost response after actual commit')
    uncertain = PostgresPortalIntents(scope, lost_response)
    with pytest.raises(IntentPersistenceFailure):
        uncertain.commit_portal_intent(intent)
    with connect() as conn:
        saved = conn.execute('SELECT job_id,attempt_id,outbox_id FROM law_app.job_intents WHERE firm_id=%s AND enrollment_id=%s',
                             (scope.firm_id, scope.enrollment_id)).fetchone()
        conn.execute('UPDATE law_app.enrollments SET active=false WHERE firm_id=%s AND enrollment_id=%s',
                     (scope.firm_id, scope.enrollment_id))
    with pytest.raises(IntentPersistenceFailure, match='unconfirmed'):
        repository.reconcile_portal_intent(intent)
    assert counts(connect, scope) == (1, 1, 1, 1)
    with connect() as conn:
        conn.execute('UPDATE law_app.enrollments SET active=true WHERE firm_id=%s AND enrollment_id=%s',
                     (scope.firm_id, scope.enrollment_id))
    found = repository.reconcile_portal_intent(intent)
    retry = repository.commit_portal_intent(intent)
    assert (found.job_id, found.attempt_id, found.outbox_id) == saved
    assert found == retry and found.reused
    assert counts(connect, scope) == (1, 1, 1, 1)


@pytest.fixture
def claimed_world(make_world, fictional_database):
    from law_app.bootstrap.fictional_postgres import build_fictional_synthetic_work
    repository, intent, connect, scope = make_world()
    receipt = repository.commit_portal_intent(intent)
    profile = FictionalPostgresProfile(True, True, database=fictional_database.database)
    work = build_fictional_synthetic_work(profile, scope, connection_factory=fictional_database.connection_factory)
    return work, receipt, connect, scope


def synthetic_result(job_id):
    return {'synthetic': True, 'review_required': True, 'job_id': str(job_id)}


def expire(connect, claim):
    with connect() as conn:
        conn.execute("UPDATE law_app.work_attempts SET expires_at=clock_timestamp()-interval '1 second' WHERE firm_id=%s AND enrollment_id=%s AND execution_id=%s",
                     (claim.scope.firm_id, claim.scope.enrollment_id, claim.execution_id))


def test_competing_claim_has_one_owner_and_one_execution(claimed_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    work, receipt, connect, scope = claimed_world
    barrier = Barrier(2)
    def acquire():
        barrier.wait(timeout=10)
        return work.claim(receipt.job_id, uuid4(), 30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(acquire) for _ in range(2)]
        claims = [future.result(timeout=10) for future in futures]
    assert sum(c is not None for c in claims) == 1
    assert next(c for c in claims if c is not None).fence == 1
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM law_app.work_attempts WHERE firm_id=%s AND enrollment_id=%s',
                            (scope.firm_id, scope.enrollment_id)).fetchone() == (1,)


@pytest.mark.parametrize('method', ['renew', 'progress', 'retry', 'complete'])
def test_expired_claim_cannot_mutate_work(claimed_world, method):
    from law_app.ports.synthetic_work import ClaimRejected
    work, receipt, connect, scope = claimed_world
    token = work.claim(receipt.job_id, uuid4(), 30)
    expire(connect, token)
    value = {'renew': 30, 'progress': {'step': 'late'}, 'retry': 0,
             'complete': synthetic_result(receipt.job_id)}[method]
    with pytest.raises(ClaimRejected):
        getattr(work, method)(token, value)
    with connect() as conn:
        assert conn.execute('SELECT state,state_version FROM law_app.job_intents WHERE job_id=%s',
                            (receipt.job_id,)).fetchone() == ('running', token.state_version)
        assert conn.execute('SELECT count(*) FROM law_app.processed_receipts WHERE job_id=%s', (receipt.job_id,)).fetchone() == (0,)


def test_takeover_retry_and_state_version_reject_stale_workers(claimed_world):
    from law_app.ports.synthetic_work import ClaimRejected
    work, receipt, connect, _ = claimed_world
    stale = work.claim(receipt.job_id, uuid4(), 30)
    expire(connect, stale)
    current = work.claim(receipt.job_id, uuid4(), 30)
    assert current.fence == stale.fence + 1 and current.execution_id != stale.execution_id
    assert current.bound_attempt_id == receipt.attempt_id
    with pytest.raises(ClaimRejected):
        work.complete(stale, synthetic_result(receipt.job_id))
    with pytest.raises(ClaimRejected):
        work.renew(stale, 30)
    renewed = work.renew(current, 30)
    with pytest.raises(ClaimRejected):
        work.progress(current, {'step': 'stale version'})
    version = work.retry(renewed, 0)
    next_claim = work.claim(receipt.job_id, uuid4(), 30)
    assert next_claim.fence == current.fence + 1 and next_claim.state_version == version + 1
    with pytest.raises(ClaimRejected):
        work.complete(renewed, synthetic_result(receipt.job_id))
    assert work.complete(next_claim, synthetic_result(receipt.job_id)).reused is False


def test_completion_retains_one_result_receipt_and_next_outbox(claimed_world):
    from law_app.ports.synthetic_work import ClaimRejected
    work, receipt, connect, scope = claimed_world
    token = work.claim(receipt.job_id, uuid4(), 30)
    token = work.renew(token, 30)
    token = work.progress(token, {'step': 'synthetic'})
    first = work.complete(token, synthetic_result(receipt.job_id))
    again = work.complete(token, synthetic_result(receipt.job_id))
    assert first.result_id == again.result_id and first.receipt_id == again.receipt_id and first.outbox_id == again.outbox_id
    assert not first.reused and again.reused
    with pytest.raises(ClaimRejected):
        work.complete(token, synthetic_result(receipt.job_id) | {'changed': True})
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM law_app.processing_results WHERE job_id=%s', (receipt.job_id,)).fetchone() == (1,)
        assert conn.execute('SELECT count(*) FROM law_app.processed_receipts WHERE job_id=%s', (receipt.job_id,)).fetchone() == (1,)
        assert conn.execute('SELECT count(*) FROM law_app.outbox_intents WHERE job_id=%s', (receipt.job_id,)).fetchone() == (2,)
        conn.execute('UPDATE law_app.enrollments SET active=false WHERE firm_id=%s AND enrollment_id=%s', (scope.firm_id, scope.enrollment_id))
    with pytest.raises(ClaimRejected):
        work.complete(token, synthetic_result(receipt.job_id))


@pytest.fixture
def original_world(fictional_database, tmp_path):
    import hashlib
    from law_app.adapters.storage.filesystem.originals import FilesystemOriginals
    from law_app.bootstrap.fictional_postgres import build_fictional_original_journey
    scope = FirmEnrollment(uuid4(), uuid4())
    intake, authority, revision = uuid4(), uuid4(), uuid4()
    data = b'Fictional intake PDF placeholder\x00\xff'
    generation, operation = uuid4().hex, uuid4().hex
    request = PortalIntentRequest(intake, generation, operation, authority,
        (RevisionReference(revision, hashlib.sha256(data).hexdigest(), len(data)),), 'fictional-original-v1', {})
    _, _, manifest = canonical_request(request)
    profile = FictionalPostgresProfile(True, True, database=fictional_database.database)
    connect = lambda: fictional_database.connection_factory(host=profile.host, database=profile.database)
    with connect() as conn, conn.transaction():
        conn.execute('INSERT INTO law_app.enrollments VALUES (%s,%s,true)', (scope.firm_id, scope.enrollment_id))
        conn.execute('INSERT INTO law_app.intakes VALUES (%s,%s,%s,true)', (scope.firm_id, scope.enrollment_id, intake))
        conn.execute("INSERT INTO law_app.portal_generations VALUES (%s,%s,%s,%s,'ready',%s,%s)",
                     (scope.firm_id, scope.enrollment_id, intake, generation, authority, manifest))
    store = FilesystemOriginals(tmp_path, scope, disposable=True)
    journey = build_fictional_original_journey(profile, scope,
        connection_factory=fictional_database.connection_factory, originals=store)
    def publish():
        return journey.publish_intent(intake, revision, data, generation_id=generation,
            operation_id=operation, authority_evidence_id=authority)
    return journey, publish, request, data, connect, scope


def test_actual_byte_upload_to_scoped_review_receipt(original_world):
    from law_app.ports.synthetic_work import ClaimRejected
    journey, publish, request, data, connect, scope = original_world
    intent = publish()
    assert publish().job_id == intent.job_id
    done = journey.process(intent.job_id, uuid4())
    # Receipt reads have current eligibility and no mutation/lease replay. Read
    # after expiry must work, while complete(token) remains unchanged/refused.
    with connect() as conn:
        conn.execute("UPDATE law_app.work_attempts SET expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (intent.job_id,))
    read, result = journey.read_review_receipt(intent.job_id)
    assert (read.result_id, read.receipt_id, read.outbox_id) == (done.result_id, done.receipt_id, done.outbox_id)
    assert result['input_sha256'] == request.revisions[0].sha256
    assert result['input_byte_length'] == len(data) and result['review_required'] is True
    with connect() as conn:
        conn.execute('UPDATE law_app.enrollments SET active=false WHERE firm_id=%s AND enrollment_id=%s', (scope.firm_id, scope.enrollment_id))
    with pytest.raises(ClaimRejected):
        journey.read_review_receipt(intent.job_id)


def test_sql_failure_after_publication_preserves_unreferenced_original(original_world):
    from law_app.ports.synthetic_work import WorkPersistenceFailure
    journey, publish, request, data, connect, scope = original_world
    # A disposable-database constraint forces a genuine revision INSERT failure
    # after complete byte publication, without fake SQL or production wiring.
    name = 'fictional_failure_' + uuid4().hex
    with connect() as conn:
        conn.execute(f"ALTER TABLE law_app.revisions ADD CONSTRAINT {name} CHECK (firm_id <> '{scope.firm_id}'::uuid)")
    try:
        with pytest.raises(WorkPersistenceFailure):
            publish()
    finally:
        with connect() as conn:
            conn.execute(f'ALTER TABLE law_app.revisions DROP CONSTRAINT {name}')
    path = journey.originals._path(request.intake_id, request.revisions[0].revision_id)
    assert path.read_bytes() == data
    assert counts(connect, scope) == (0, 0, 0, 0)
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM law_app.revisions WHERE firm_id=%s AND enrollment_id=%s',
                            (scope.firm_id, scope.enrollment_id)).fetchone() == (0,)


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_missing_or_corrupt_committed_original_cannot_complete(original_world, damage):
    from law_app.ports.originals import OriginalUnavailable
    journey, publish, request, _, connect, _ = original_world
    intent = publish()
    path = journey.originals._path(request.intake_id, request.revisions[0].revision_id)
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'corrupted')
    with pytest.raises(OriginalUnavailable):
        journey.process(intent.job_id, uuid4())
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM law_app.processed_receipts WHERE job_id=%s', (intent.job_id,)).fetchone() == (0,)


def test_foreign_scope_cannot_read_review_receipt(original_world):
    from law_app.adapters.persistence.postgres.original_revisions import PostgresOriginalRevisions
    from law_app.adapters.storage.filesystem.originals import FilesystemOriginals
    from law_app.ports.synthetic_work import ClaimRejected
    journey, publish, _, _, connect, scope = original_world
    intent = publish()
    journey.process(intent.job_id, uuid4())
    foreign = replace(scope, firm_id=uuid4())
    store = FilesystemOriginals(journey.originals.root, foreign, disposable=True)
    with pytest.raises(ClaimRejected):
        PostgresOriginalRevisions(foreign, connect, store).read_review_receipt(intent.job_id)


@pytest.fixture
def original_delivery(original_world, fictional_database):
    from law_app.bootstrap.fictional_postgres import build_fictional_outbox_delivery
    journey, publish, request, data, connect, scope = original_world
    profile = FictionalPostgresProfile(True, True, database=fictional_database.database)
    delivery = build_fictional_outbox_delivery(profile, scope, connection_factory=fictional_database.connection_factory)
    return delivery, journey, publish(), connect, scope


def expire_delivery(connect, token):
    with connect() as conn:
        conn.execute("UPDATE law_app.outbox_intents SET delivery_expires_at=clock_timestamp()-interval '1 second' WHERE firm_id=%s AND enrollment_id=%s AND outbox_id=%s",
                     (token.scope.firm_id, token.scope.enrollment_id, token.outbox_id))


@pytest.mark.parametrize('scan', [False, True])
def test_competing_delivery_claims_have_one_owner(original_delivery, scan):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    delivery, _, intent, _, _ = original_delivery
    barrier = Barrier(2)
    def claim():
        barrier.wait(timeout=10)
        return (delivery.claim_next(uuid4(), 30) if scan else
                delivery.claim(intent.outbox_id, uuid4(), 30))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        tokens = [future.result(timeout=10) for future in futures]
    assert sum(token is not None for token in tokens) == 1
    token = next(token for token in tokens if token is not None)
    assert token.fence == 1 and token.job_id == intent.job_id and token.outbox_id == intent.outbox_id


def test_expired_or_stale_delivery_cannot_acknowledge_or_complete(original_delivery):
    from law_app.ports.synthetic_work import ClaimRejected
    delivery, _, intent, connect, _ = original_delivery
    old = delivery.claim(intent.outbox_id, uuid4(), 30)
    expire_delivery(connect, old)
    with pytest.raises(ClaimRejected):
        delivery.acknowledge(old)
    current = delivery.claim(intent.outbox_id, uuid4(), 30)
    assert current.delivery_id != old.delivery_id and current.fence == old.fence + 1
    with pytest.raises(ClaimRejected):
        delivery.acknowledge(old)
    delivery.acknowledge(current)
    # Delivery acknowledgment is independent evidence; it fabricates neither a
    # result/receipt nor a done processing state.
    with connect() as conn:
        assert conn.execute('SELECT state FROM law_app.job_intents WHERE job_id=%s', (intent.job_id,)).fetchone() == ('intent',)
        assert conn.execute('SELECT count(*) FROM law_app.processed_receipts WHERE job_id=%s', (intent.job_id,)).fetchone() == (0,)
    assert delivery.claim(intent.outbox_id, uuid4(), 30) is None


def test_success_then_lost_ack_redelivery_reuses_scoped_review_receipt(original_delivery):
    from law_app.adapters.persistence.postgres.outbox_delivery import deliver_fictional_once
    from law_app.ports.synthetic_work import ClaimRejected
    delivery, journey, intent, connect, scope = original_delivery
    observed = []
    normal_handler = journey.delivery_handler(uuid4())
    def handler_then_lost_ack(token):
        # Separate connection acquires job lock inside callback: the delivery
        # claim transaction must already be released before invoking it.
        with connect() as conn, conn.transaction():
            conn.execute("SET LOCAL lock_timeout='1s'")
            conn.execute('SELECT job_id FROM law_app.job_intents WHERE job_id=%s FOR UPDATE', (token.job_id,))
        observed.append(normal_handler(token))
        expire_delivery(connect, token)
        return observed[-1]
    with pytest.raises(ClaimRejected):
        deliver_fictional_once(delivery, intent.outbox_id, uuid4(), handler_then_lost_ack)
    result = deliver_fictional_once(delivery, intent.outbox_id, uuid4(), normal_handler)
    assert result == observed[0]
    assert counts(connect, scope) == (1, 1, 1, 2)
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM law_app.processing_results WHERE job_id=%s', (intent.job_id,)).fetchone() == (1,)
        assert conn.execute('SELECT state,delivery_fence FROM law_app.outbox_intents WHERE outbox_id=%s', (intent.outbox_id,)).fetchone() == ('delivered', 2)
    with pytest.raises(ClaimRejected):
        delivery.claim(result[0].outbox_id, uuid4(), 30)  # portal.processed.v1 excluded


def test_separate_service_worker_processes_preserve_durable_scoped_journey(admission_world, fictional_database):
    import base64
    import hashlib
    import json
    service, request, data, connect, scope = admission_world
    config = {'enabled': True, 'disposable': True, 'host': '127.0.0.1', 'database': fictional_database.database,
        'port': fictional_database.port, 'originals_root': str(service.journey.originals.root),
        'firm_id': str(scope.firm_id), 'enrollment_id': str(scope.enrollment_id),
        'actor_id': str(service.actor.actor_id), 'role': 'service', 'pending_work_cap': 3}
    module = 'law_app.interfaces.fictional_cli'
    pids = []
    def run(command, payload, *, operator=config, target=module, succeeds=True):
        response = fictional_database.run_cli(target, operator, command, payload)
        pids.append(response['pid'])
        assert response['returncode'] == (0 if succeeds else 2)
        if not succeeds:
            assert response['stdout'] == ''
            assert response['stderr'] == 'Fictional command unavailable; outcome may be unconfirmed.\n'
            return None
        assert response['stderr'] == ''
        return response['result']
    upload = {'intake_id': str(request.intake_id), 'revision_id': str(request.revision_id),
        'data_b64': base64.b64encode(data).decode('ascii'), 'generation_id': request.generation_id,
        'operation_id': request.operation_id, 'expected_current_generation': None}
    evidence = fictional_database.evidence_directory
    request_path = evidence / 'upload-request.json'
    request_path.write_text(json.dumps(upload, sort_keys=True), encoding='utf-8')
    def retained_request():
        # Resume consumes the original credential-free request; no reconstruction.
        assert request_path.is_file()
        value = json.loads(request_path.read_text(encoding='utf-8'))
        assert value == upload
        return value
    scope_params = (scope.firm_id, scope.enrollment_id)
    with connect() as conn:
        for table in ('intakes', 'portal_generations', 'job_intents', 'fictional_admissions'):
            assert conn.execute('SELECT count(*) FROM law_app.' + table + ' WHERE firm_id=%s AND enrollment_id=%s', scope_params).fetchone() == (0,)
    accepted = run('upload', retained_request())
    history = run('admission-status',upload)
    assert history['state']=='committed' and history['original_bytes']=='verified' and history['historical_only']
    original_files = [p for p in service.journey.originals.root.rglob('*') if p.is_file()]
    assert len(original_files) == 1
    original = original_files[0]
    assert original.read_bytes() == data
    original_sha = hashlib.sha256(original.read_bytes()).hexdigest()
    assert history['original']['sha256'] == original_sha
    admission_path = evidence / 'admission-receipt.json'
    admission_path.write_text(json.dumps({'accepted':accepted,'history':history,'original_path':str(original),
        'original_sha256':original_sha}, sort_keys=True), encoding='utf-8')
    fictional_database.restart_fixture()
    retained = json.loads(admission_path.read_text(encoding='utf-8'))
    assert run('admission-status', retained_request()) == retained['history']
    resumed = run('upload', retained_request())
    assert resumed == dict(retained['accepted'], reused=True)
    assert history['retained_intent'] == {key:accepted[key] for key in ('job_id','attempt_id','outbox_id')}
    assert original.read_bytes() == data
    run('abandon',retained_request(),succeeds=False)
    job = {'job_id': accepted['job_id']}
    assert run('status', job) == dict(job, status='queued', review_required=False)
    # Fresh worker discovers committed work after the same cluster/database restart.
    worker = dict(config, role='worker', actor_id=str(uuid4()))
    worker_module = 'law_app.interfaces.worker.fictional'
    finished = run('once', {}, operator=worker, target=worker_module)
    assert finished['delivery'] == 'acknowledged' and finished['review_required'] is True
    assert run('status', job) == dict(job, status='review_required', review_required=True)
    review = run('review', job)
    assert review['receipt_id'] == finished['receipt_id'] and review['result_id'] == finished['result_id']
    assert review['result']['input_sha256'] == hashlib.sha256(data).hexdigest()
    review_path = evidence / 'review-receipt.json'
    review_path.write_text(json.dumps(review, sort_keys=True), encoding='utf-8')
    fictional_database.restart_fixture()
    assert run('review', job) == json.loads(review_path.read_text(encoding='utf-8'))
    assert run('status', job) == dict(job, status='review_required', review_required=True)
    assert original.read_bytes() == data and hashlib.sha256(original.read_bytes()).hexdigest() == original_sha
    assert run('once', {'outbox_id': accepted['outbox_id']}, operator=worker, target=worker_module) == {'delivery': 'not_claimed'}
    assert run('once', {}, operator=worker, target=worker_module) == {'delivery': 'nothing_claimed_within_scan', 'scan_limit': 16}
    run('status', job, operator=dict(config, firm_id=str(uuid4())), succeeds=False)
    run('status', job, operator=worker, succeeds=False)
    run('once', {'outbox_id': accepted['outbox_id']}, target=worker_module, succeeds=False)
    assert len(set(pids)) == len(pids)
    assert counts(connect, scope) == (1, 1, 1, 2)

    with connect() as conn:
        for table in ('fictional_admissions','job_intents','processing_attempts','processing_results','processed_receipts'):
            assert conn.execute('SELECT count(*) FROM law_app.' + table + ' WHERE firm_id=%s AND enrollment_id=%s',scope_params).fetchone() == (1,)
        outboxes = conn.execute('SELECT outbox_id,event_kind FROM law_app.outbox_intents WHERE firm_id=%s AND enrollment_id=%s',scope_params).fetchall()
        assert {row[1] for row in outboxes} == {'portal.job_intent.v1','portal.processed.v1'}
        assert len({row[0] for row in outboxes}) == 2
        assert conn.execute('SELECT job_id,bound_attempt_id,result_id,receipt_id,completion_outbox_id FROM law_app.processed_receipts WHERE firm_id=%s AND enrollment_id=%s',scope_params).fetchone() == (
            UUID(accepted['job_id']), UUID(accepted['attempt_id']),
            UUID(review['result_id']), UUID(review['receipt_id']),
            next(row[0] for row in outboxes if row[1]=='portal.processed.v1'))
    assert fictional_database.restart_count == 2
    (evidence/'journey-verified.json').write_text(json.dumps({'orderly_restarts':2,'subprocesses':len(pids),
        'admissions':1,'jobs':1,'bound_attempts':1,'results':1,'receipts':1,'outboxes':2,
        'original_sha256':original_sha,'original_bytes_unchanged':True,'accepted':accepted,'review':review},sort_keys=True),encoding='utf-8')


def test_pending_selection_excludes_foreign_delivered_processed_and_live_lease(original_world, fictional_database, make_world):
    from law_app.bootstrap.fictional_postgres import build_fictional_outbox_delivery
    journey, publish, request, data, connect, scope = original_world
    profile = FictionalPostgresProfile(True, True, database=fictional_database.database)
    delivery = build_fictional_outbox_delivery(profile, scope, connection_factory=fictional_database.connection_factory)
    eligible = publish()
    def another():
        return journey.publish_intent(request.intake_id, request.revisions[0].revision_id, data,
            generation_id=request.generation_id, operation_id=uuid4().hex, authority_evidence_id=request.authority_evidence_id)
    delivered = another()
    delivery.acknowledge(delivery.claim(delivered.outbox_id, uuid4(), 30))
    completed = another()
    processed = journey.process(completed.job_id, uuid4())
    delivery.acknowledge(delivery.claim(completed.outbox_id, uuid4(), 30))
    live = another()
    assert delivery.claim(live.outbox_id, uuid4(), 30) is not None
    foreign_repo, foreign_request, _, _ = make_world()
    foreign = foreign_repo.commit_portal_intent(foreign_request)
    selected = delivery.pending_ids()
    assert selected == (eligible.outbox_id,)
    assert not set(selected) & {delivered.outbox_id, completed.outbox_id, processed.outbox_id, live.outbox_id, foreign.outbox_id}


def test_bounded_scan_reaches_eligible_after_currently_revoked_candidate(original_world, fictional_database):
    from law_app.bootstrap.fictional_postgres import build_fictional_outbox_delivery
    journey, publish, old, data, connect, scope = original_world
    rejected = publish()
    intake, revision, authority = uuid4(), uuid4(), uuid4()
    request = replace(old, intake_id=intake, generation_id=uuid4().hex, operation_id=uuid4().hex,
        authority_evidence_id=authority, revisions=(replace(old.revisions[0], revision_id=revision),))
    _, _, manifest = canonical_request(request)
    with connect() as conn, conn.transaction():
        conn.execute('UPDATE law_app.intakes SET open=false WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s',
                     (scope.firm_id, scope.enrollment_id, old.intake_id))
        conn.execute('INSERT INTO law_app.intakes VALUES (%s,%s,%s,true)', (scope.firm_id, scope.enrollment_id, intake))
        conn.execute("INSERT INTO law_app.portal_generations VALUES (%s,%s,%s,%s,'ready',%s,%s)",
            (scope.firm_id, scope.enrollment_id, intake, request.generation_id, authority, manifest))
    eligible = journey.publish_intent(intake, revision, data, generation_id=request.generation_id,
        operation_id=request.operation_id, authority_evidence_id=authority)
    profile = FictionalPostgresProfile(True, True, database=fictional_database.database)
    delivery = build_fictional_outbox_delivery(profile, scope, connection_factory=fictional_database.connection_factory)
    assert delivery.pending_ids(scan_limit=2) == (rejected.outbox_id, eligible.outbox_id)
    assert delivery.claim_next(uuid4(), 30, scan_limit=1) is None
    token = delivery.claim_next(uuid4(), 30, scan_limit=2)
    assert token.outbox_id == eligible.outbox_id and token.job_id == eligible.job_id


@pytest.fixture
def admission_world(fictional_database, tmp_path):
    from law_app.application.intake.fictional_service import FictionalActor, FictionalIntakeService
    from law_app.adapters.persistence.postgres.fictional_admissions import PostgresFictionalAdmissions
    from law_app.adapters.storage.filesystem.originals import FilesystemOriginals
    from law_app.bootstrap.fictional_postgres import build_fictional_original_journey
    from law_app.ports.fictional_admissions import AdmissionRequest
    scope = FirmEnrollment(uuid4(),uuid4())
    actor = FictionalActor(scope,uuid4(),'service')
    profile = FictionalPostgresProfile(True,True,database=fictional_database.database)
    connect = lambda: fictional_database.connection_factory(host=profile.host,database=profile.database)
    with connect() as conn:
        conn.execute('INSERT INTO law_app.enrollments VALUES (%s,%s,true)',(scope.firm_id,scope.enrollment_id))
    originals = FilesystemOriginals(getattr(fictional_database,'originals_directory',tmp_path),scope,disposable=True)
    journey = build_fictional_original_journey(profile,scope,connection_factory=fictional_database.connection_factory,originals=originals)
    admissions = PostgresFictionalAdmissions(actor,journey,3)
    service = FictionalIntakeService(actor,journey,admissions)
    request = AdmissionRequest(uuid4(),uuid4(),uuid4().hex,uuid4().hex,None)
    return service,request,b'Fictional admission bytes\x00\xff',connect,scope


def admitted_upload(service,request,data):
    return service.upload(request.intake_id,request.revision_id,data,generation_id=request.generation_id,
        operation_id=request.operation_id,expected_current_generation=request.expected_current_generation)


def test_admission_enrollment_only_upload_reuses_issued_evidence_and_conflicts(admission_world):
    from law_app.ports.fictional_admissions import AdmissionConflict
    service,request,data,connect,scope = admission_world
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM law_app.intakes WHERE firm_id=%s AND enrollment_id=%s',(scope.firm_id,scope.enrollment_id)).fetchone() == (0,)
    first=admitted_upload(service,request,data)
    second=admitted_upload(service,request,data)
    assert first.job_id==second.job_id and second.reused
    with pytest.raises(AdmissionConflict):
        admitted_upload(service,request,b'changed')
    with connect() as conn:
        row=conn.execute('SELECT evidence_id,actor_id,purpose,manifest_sha256,state,request_json FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s',(scope.firm_id,scope.enrollment_id)).fetchone()
        assert row[1:3] == (service.actor.actor_id,'fictional_intake_upload') and row[4]=='committed'
        assert conn.execute('SELECT authority_evidence_id,revision_manifest_sha256 FROM law_app.portal_generations WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s',(scope.firm_id,scope.enrollment_id,request.intake_id)).fetchone()==(row[0],row[3])
    assert counts(connect,scope)==(1,1,1,1)


def test_concurrent_admission_capacity_cannot_overreserve(admission_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from law_app.adapters.persistence.postgres.fictional_admissions import PostgresFictionalAdmissions
    from law_app.ports.fictional_admissions import AdmissionCapacity
    service,request,data,connect,scope=admission_world
    admissions=PostgresFictionalAdmissions(service.actor,service.journey,1)
    barrier=Barrier(2)
    def reserve(op):
        barrier.wait(timeout=10)
        try:
            return admissions.reserve(replace(request,intake_id=uuid4(),operation_id=op),data)
        except AdmissionCapacity:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(reserve,uuid4().hex) for _ in range(2)]
        outcomes=[future.result(timeout=10) for future in futures]
    assert sum(value is not None for value in outcomes)==1
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s AND state='reserved'",(scope.firm_id,scope.enrollment_id)).fetchone()==(1,)
    assert list(service.journey.originals.root.iterdir())==[]


def test_competing_admission_replacements_only_one_current_generation(admission_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from law_app.ports.fictional_admissions import AdmissionRejected
    service,request,data,connect,scope=admission_world
    first=admitted_upload(service,request,data)
    replacements=[replace(request,revision_id=uuid4(),generation_id=uuid4().hex,operation_id=uuid4().hex,expected_current_generation=request.generation_id) for _ in range(2)]
    reservations=[service.admissions.reserve(r,data) for r in replacements]
    revisions=[service.journey.originals.publish(r.intake_id,r.revision_id,data) for r in replacements]
    barrier=Barrier(2)
    def finish(n):
        barrier.wait(timeout=10)
        try:
            return service.admissions.finish(reservations[n],revisions[n])
        except AdmissionRejected:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=[f.result(timeout=10) for f in [pool.submit(finish,n) for n in range(2)]]
    assert sum(value is not None for value in outcomes)==1
    with connect() as conn:
        states=conn.execute('SELECT generation_id,state FROM law_app.portal_generations WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s',(scope.firm_id,scope.enrollment_id,request.intake_id)).fetchall()
        assert (request.generation_id,'superseded') in states and sum(s=='ready' for _,s in states)==1
        assert conn.execute("SELECT count(*) FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s AND state='reserved'",(scope.firm_id,scope.enrollment_id)).fetchone()==(1,)
    assert counts(connect,scope)==(2,2,2,2)


def test_admission_publication_failure_keeps_reservation_no_runnable_partial(admission_world,monkeypatch):
    service,request,data,connect,scope=admission_world
    def fail(*args):
        raise OSError('Fictional publication interrupted')
    monkeypatch.setattr(service.journey.originals,'publish',fail)
    with pytest.raises(OSError):
        admitted_upload(service,request,data)
    assert counts(connect,scope)==(0,0,0,0)
    with connect() as conn:
        assert conn.execute("SELECT state FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s",(scope.firm_id,scope.enrollment_id)).fetchone()==('reserved',)
        assert conn.execute('SELECT count(*) FROM law_app.portal_generations WHERE firm_id=%s AND enrollment_id=%s',(scope.firm_id,scope.enrollment_id)).fetchone()==(0,)


@pytest.mark.parametrize('replacement',[False,True])
def test_admission_final_sql_failure_keeps_bytes_pending_and_retryable(admission_world,replacement):
    from law_app.ports.fictional_admissions import AdmissionPersistenceFailure
    service,request,data,connect,scope=admission_world
    previous=None
    if replacement:
        admitted_upload(service,request,data)
        previous=request.generation_id
        request=replace(request,revision_id=uuid4(),generation_id=uuid4().hex,operation_id=uuid4().hex,
            expected_current_generation=previous)
    name='fictional_admission_failure_'+uuid4().hex
    with connect() as conn:
        conn.execute(f"ALTER TABLE law_app.outbox_intents ADD CONSTRAINT {name} CHECK (firm_id <> '{scope.firm_id}'::uuid) NOT VALID")
    try:
        with pytest.raises(AdmissionPersistenceFailure):
            admitted_upload(service,request,data)
    finally:
        with connect() as conn:
            conn.execute(f'ALTER TABLE law_app.outbox_intents DROP CONSTRAINT {name}')
    assert service.journey.originals._path(request.intake_id,request.revision_id).read_bytes()==data
    assert counts(connect,scope)==((1,1,1,1) if replacement else (0,0,0,0))
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM law_app.portal_generations WHERE firm_id=%s AND enrollment_id=%s',(scope.firm_id,scope.enrollment_id)).fetchone()==((1,) if replacement else (0,))
        if replacement:
            assert conn.execute('SELECT state FROM law_app.portal_generations WHERE firm_id=%s AND enrollment_id=%s AND generation_id=%s',(scope.firm_id,scope.enrollment_id,previous)).fetchone()==('ready',)
        retained=conn.execute('SELECT reservation_id,evidence_id,state FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s AND operation_id=%s',(scope.firm_id,scope.enrollment_id,request.operation_id)).fetchone()
        assert retained[2]=='reserved'
    admitted_upload(service,request,data)
    with connect() as conn:
        assert conn.execute('SELECT reservation_id,evidence_id FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s AND operation_id=%s',(scope.firm_id,scope.enrollment_id,request.operation_id)).fetchone()==retained[:2]


def test_admission_lost_final_response_reconciles_same_ids_after_current_access(admission_world,monkeypatch):
    from contextlib import contextmanager
    from law_app.ports.fictional_admissions import AdmissionPersistenceFailure,AdmissionRejected
    service,request,data,connect,scope=admission_world
    reservation=service.admissions.reserve(request,data)
    revision=service.journey.originals.publish(request.intake_id,request.revision_id,data)
    @contextmanager
    def lost_response():
        with connect() as conn:
            yield conn
        raise ConnectionError('Fictional final response lost after actual commit')
    monkeypatch.setattr(service.journey.work,'_connect',lost_response)
    with pytest.raises(AdmissionPersistenceFailure,match='unconfirmed'):
        service.admissions.finish(reservation,revision)
    monkeypatch.setattr(service.journey.work,'_connect',connect)
    with connect() as conn:
        saved=conn.execute('SELECT job_id,attempt_id,outbox_id FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s',(scope.firm_id,scope.enrollment_id)).fetchone()
        conn.execute('UPDATE law_app.enrollments SET active=false WHERE firm_id=%s AND enrollment_id=%s',(scope.firm_id,scope.enrollment_id))
    with pytest.raises(AdmissionRejected):
        admitted_upload(service,request,data)
    assert counts(connect,scope)==(1,1,1,1)
    with connect() as conn:
        conn.execute('UPDATE law_app.enrollments SET active=true WHERE firm_id=%s AND enrollment_id=%s',(scope.firm_id,scope.enrollment_id))
    reconciled=admitted_upload(service,request,data)
    assert (reconciled.job_id,reconciled.attempt_id,reconciled.outbox_id)==saved and reconciled.reused


def test_reserved_abandonment_is_terminal_idempotent_and_releases_only_its_capacity(admission_world):
    from law_app.adapters.persistence.postgres.fictional_admissions import PostgresFictionalAdmissions
    from law_app.ports.fictional_admissions import AdmissionRejected,AdmissionCapacity
    service,request,data,connect,scope=admission_world
    admissions=PostgresFictionalAdmissions(service.actor,service.journey,1)
    reservation=admissions.reserve(request,data)
    first=admissions.abandon(request,data)
    again=admissions.abandon(request,data)
    assert first.reservation==again.reservation==reservation and first.abandoned_at==again.abandoned_at
    assert not first.reused and again.reused
    status=admissions.status(request,data)
    assert status.state=='abandoned' and status.historical_only and status.original_bytes=='unavailable'
    with pytest.raises(AdmissionRejected):
        admissions.reserve(request,data)
    second=replace(request,intake_id=uuid4(),revision_id=uuid4(),generation_id=uuid4().hex,operation_id=uuid4().hex)
    second_reservation=admissions.reserve(second,data)
    admissions.abandon(request,data)
    with pytest.raises(AdmissionCapacity):
        admissions.reserve(replace(second,operation_id=uuid4().hex),data)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s AND state='reserved'",(scope.firm_id,scope.enrollment_id)).fetchone()==(1,)
    assert counts(connect,scope)==(0,0,0,0)
    second_revision=service.journey.originals.publish(second.intake_id,second.revision_id,data)
    admissions.finish(second_reservation,second_revision)
    with pytest.raises(AdmissionRejected):
        admissions.abandon(second,data)
    with pytest.raises(AdmissionCapacity):
        admissions.reserve(replace(second,intake_id=uuid4(),revision_id=uuid4(),generation_id=uuid4().hex,operation_id=uuid4().hex),data)
    assert counts(connect,scope)==(1,1,1,1)


def test_abandon_vs_finish_serializes_to_one_terminal_outcome(admission_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from law_app.ports.fictional_admissions import AdmissionRejected
    service,request,data,connect,scope=admission_world
    reservation=service.admissions.reserve(request,data)
    revision=service.journey.originals.publish(request.intake_id,request.revision_id,data)
    barrier=Barrier(2)
    def run(action):
        barrier.wait(timeout=10)
        try:
            result=(service.admissions.finish(reservation,revision) if action=='finish'
                    else service.admissions.abandon(request,data))
            return action,result
        except AdmissionRejected:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[f.result(timeout=10) for f in [pool.submit(run,action) for action in ('finish','abandon')]]
    assert sum(result is not None for result in results)==1
    winner=next(result[0] for result in results if result is not None)
    status=service.admissions.status(request,data)
    assert status.state==('committed' if winner=='finish' else 'abandoned')
    assert counts(connect,scope)==((1,1,1,1) if winner=='finish' else (0,0,0,0))
    assert service.journey.originals.verified_read(revision)==data


def test_publication_after_abandonment_preserves_bytes_but_cannot_finalize(admission_world):
    from law_app.ports.fictional_admissions import AdmissionRejected
    service,request,data,connect,scope=admission_world
    reservation=service.admissions.reserve(request,data)
    service.admissions.abandon(request,data)
    revision=service.journey.originals.publish(request.intake_id,request.revision_id,data)
    with pytest.raises(AdmissionRejected):
        service.admissions.finish(reservation,revision)
    assert service.admissions.status(request,data).original_bytes=='verified'
    assert counts(connect,scope)==(0,0,0,0)


@pytest.mark.parametrize('foreign',['actor','scope'])
def test_admission_history_and_abandonment_refuse_foreign_binding(admission_world,foreign,fictional_database,make_world):
    from law_app.application.intake.fictional_service import FictionalActor
    from law_app.adapters.persistence.postgres.fictional_admissions import PostgresFictionalAdmissions
    from law_app.adapters.storage.filesystem.originals import FilesystemOriginals
    from law_app.bootstrap.fictional_postgres import build_fictional_original_journey
    from law_app.ports.fictional_admissions import AdmissionRejected
    service,request,data,connect,scope=admission_world
    service.admissions.reserve(request,data)
    if foreign=='actor':
        actor=replace(service.actor,actor_id=uuid4())
        journey=service.journey
    else:
        _,_,_,other_scope=make_world()
        actor=FictionalActor(other_scope,uuid4(),'service')
        store=FilesystemOriginals(service.journey.originals.root,other_scope,disposable=True)
        profile=FictionalPostgresProfile(True,True,database=fictional_database.database)
        journey=build_fictional_original_journey(profile,other_scope,connection_factory=fictional_database.connection_factory,originals=store)
    foreign_repo=PostgresFictionalAdmissions(actor,journey,3)
    for action in (foreign_repo.status,foreign_repo.abandon):
        with pytest.raises(AdmissionRejected):
            action(request,data)
    assert service.admissions.status(request,data).state=='reserved'


def test_stale_expected_head_history_does_not_reauthorize_old_work(admission_world):
    from law_app.ports.synthetic_work import ClaimRejected
    service,request,data,connect,scope=admission_world
    first=admitted_upload(service,request,data)
    pending=replace(request,revision_id=uuid4(),generation_id=uuid4().hex,operation_id=uuid4().hex,
        expected_current_generation=request.generation_id)
    service.admissions.reserve(pending,data)
    winner=replace(pending,revision_id=uuid4(),generation_id=uuid4().hex,operation_id=uuid4().hex)
    admitted_upload(service,winner,data)
    status=service.admissions.status(pending,data)
    assert status.state=='stale_expected_head' and status.lifecycle=='reserved' and not status.head_matches
    historical=service.admissions.status(request,data)
    assert historical.state=='committed' and historical.historical_only and not historical.head_matches
    with pytest.raises(ClaimRejected):
        service.review(first.job_id)
    service.admissions.abandon(pending,data)
    assert service.admissions.status(pending,data).state=='abandoned'


def test_committed_history_missing_bytes_is_not_processing_success(admission_world):
    from law_app.ports.originals import OriginalUnavailable
    from law_app.ports.fictional_admissions import AdmissionRejected
    from law_app.ports.synthetic_work import ClaimRejected
    service,request,data,connect,scope=admission_world
    receipt=admitted_upload(service,request,data)
    service.journey.originals._path(request.intake_id,request.revision_id).unlink()
    with pytest.raises(OriginalUnavailable):
        service.review(receipt.job_id)
    with connect() as conn:
        conn.execute('UPDATE law_app.enrollments SET active=false WHERE firm_id=%s AND enrollment_id=%s',(scope.firm_id,scope.enrollment_id))
    history=service.admissions.status(request,data)
    assert history.state=='committed' and history.original_bytes=='unavailable' and history.historical_only
    assert history.receipt.job_id==receipt.job_id
    with pytest.raises(ClaimRejected):
        service.review(receipt.job_id)
    with pytest.raises(AdmissionRejected):
        service.admissions.abandon(request,data)


def test_abandonment_lost_response_reconciles_tombstone_without_reopen(admission_world,monkeypatch):
    from contextlib import contextmanager
    from law_app.ports.fictional_admissions import AdmissionPersistenceFailure,AdmissionRejected
    service,request,data,connect,scope=admission_world
    reservation=service.admissions.reserve(request,data)
    @contextmanager
    def lost_response():
        with connect() as conn:
            yield conn
        raise ConnectionError('Fictional abandonment response lost after actual commit')
    monkeypatch.setattr(service.journey.work,'_connect',lost_response)
    with pytest.raises(AdmissionPersistenceFailure):
        service.admissions.abandon(request,data)
    with pytest.raises(AdmissionPersistenceFailure):
        service.admissions.status(request,data)
    monkeypatch.setattr(service.journey.work,'_connect',connect)
    status=service.admissions.status(request,data)
    assert status.state=='abandoned' and status.reservation==reservation
    assert service.admissions.abandon(request,data).reused
    with pytest.raises(AdmissionRejected):
        admitted_upload(service,request,data)
    assert counts(connect,scope)==(0,0,0,0)
