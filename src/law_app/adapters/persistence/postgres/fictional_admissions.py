"""Two short admission transactions around verified immutable publication."""
from contextlib import contextmanager
import hashlib
import json
from uuid import UUID, uuid4

from law_app.ports.fictional_admissions import (
    AdmissionAbandonment, AdmissionCapacity, AdmissionConflict, AdmissionPersistenceFailure, AdmissionRejected,
    AdmissionReservation, AdmissionStatus, PURPOSE, canonical_admission,
)
from law_app.ports.originals import OriginalUnavailable
from law_app.ports.portal_intents import PortalIntentReceipt, PortalIntentRequest, canonical_request
from law_app.ports.synthetic_work import WorkPersistenceFailure


class PostgresFictionalAdmissions:
    def __init__(self, actor, journey, pending_work_cap):
        if actor.role != 'service' or actor.scope != journey.work.scope:
            raise ValueError('Trusted fictional service actor/scope required')
        if type(pending_work_cap) is not int or not 1 <= pending_work_cap <= 2**31-1:
            raise ValueError('Explicit bounded per-enrollment pending-work cap required')
        self.actor, self.journey, self.cap = actor, journey, pending_work_cap
        self.scope = actor.scope

    @contextmanager
    def _transaction(self):
        try:
            with self.journey.work._cursor() as cursor:
                cursor.execute('SELECT max(version) FROM law_app.schema_versions')
                if cursor.fetchone() != (5,):
                    raise ValueError('Fictional admission requires schema v5')
                yield cursor
        except WorkPersistenceFailure:
            raise AdmissionPersistenceFailure('Admission outcome remains unconfirmed') from None

    def _enrollment(self, cursor):
        scope = (self.scope.firm_id, self.scope.enrollment_id)
        # Serialize both reservation capacity and final generation transitions.
        cursor.execute('SELECT active FROM law_app.enrollments WHERE firm_id=%s AND enrollment_id=%s FOR UPDATE', scope)
        if cursor.fetchone() != (True,):
            raise AdmissionRejected('Admission unavailable')
        return scope

    def _expected(self, cursor, scope, intake, expected):
        key = scope + (intake,)
        cursor.execute('SELECT current_generation_id FROM law_app.fictional_intake_heads WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s FOR UPDATE', key)
        head = cursor.fetchone()
        if head is None or head[0] != expected:
            raise AdmissionRejected('Expected current generation unavailable')
        cursor.execute('SELECT open FROM law_app.intakes WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s FOR SHARE', key)
        existing = cursor.fetchone()
        if existing is not None and existing != (True,):
            raise AdmissionRejected('Admission unavailable')

    def reserve(self, request, data):
        payload, digest, manifest, revision = canonical_admission(self.actor, request, data)
        with self._transaction() as cursor:
            scope = self._enrollment(cursor)
            cursor.execute('INSERT INTO law_app.fictional_intake_limits VALUES (%s,%s,%s) ON CONFLICT (firm_id,enrollment_id) DO NOTHING', scope + (self.cap,))
            cursor.execute('SELECT pending_work_cap FROM law_app.fictional_intake_limits WHERE firm_id=%s AND enrollment_id=%s', scope)
            if cursor.fetchone() != (self.cap,):
                raise ValueError('Configured cap differs from retained enrollment cap')
            cursor.execute('SELECT reservation_id,evidence_id,request_sha256,state FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s AND operation_id=%s FOR UPDATE',
                scope + (request.intake_id, request.operation_id))
            existing = cursor.fetchone()
            if existing is not None:
                if existing[2] != digest:
                    raise AdmissionConflict('Admission operation inputs changed')
                if existing[3] == 'abandoned':
                    raise AdmissionRejected('Admission operation is terminal')
                reservation = AdmissionReservation(*existing[:3], revision)
            else:
                cursor.execute('INSERT INTO law_app.fictional_intake_heads VALUES (%s,%s,%s,NULL) ON CONFLICT (firm_id,enrollment_id,intake_id) DO NOTHING', scope + (request.intake_id,))
                self._expected(cursor, scope, request.intake_id, request.expected_current_generation)
                cursor.execute("""SELECT
                    (SELECT count(*) FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s AND state='reserved') +
                    (SELECT count(*) FROM law_app.job_intents WHERE firm_id=%s AND enrollment_id=%s AND state IN ('intent','running','retry'))""", scope + scope)
                if cursor.fetchone()[0] >= self.cap:
                    raise AdmissionCapacity('Pending-work capacity unavailable')
                reservation_id, evidence_id = uuid4(), uuid4()
                cursor.execute("""INSERT INTO law_app.fictional_admissions
                    (firm_id,enrollment_id,reservation_id,evidence_id,intake_id,generation_id,operation_id,
                     actor_id,purpose,request_sha256,request_json,manifest_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                    scope + (reservation_id, evidence_id, request.intake_id, request.generation_id,
                    request.operation_id, self.actor.actor_id, PURPOSE, digest, payload, manifest))
                reservation = AdmissionReservation(reservation_id, evidence_id, digest, revision)
        return reservation

    def finish(self, reservation, revision):
        if reservation.revision != revision or revision.scope != self.scope:
            raise AdmissionRejected('Exact reserved original unavailable')
        # All filesystem work precedes this short SQL transaction.
        self.journey.originals.verified_read(revision)
        with self._transaction() as cursor:
            scope = self._enrollment(cursor)
            cursor.execute("""SELECT evidence_id,request_sha256,request_json,manifest_sha256,state,job_id,attempt_id,outbox_id
                FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s AND reservation_id=%s FOR UPDATE""",
                scope + (reservation.reservation_id,))
            saved = cursor.fetchone()
            if saved is None or saved[:2] != (reservation.evidence_id, reservation.request_sha256):
                raise AdmissionRejected('Admission reservation unavailable')
            frozen, manifest, state = saved[2:5]
            if state == 'abandoned':
                raise AdmissionRejected('Admission operation is terminal')
            frozen_digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(',',':'), allow_nan=False).encode('utf-8')).hexdigest()
            if (frozen_digest != reservation.request_sha256
                    or frozen['actor_id'] != str(self.actor.actor_id) or frozen['purpose'] != PURPOSE
                    or frozen['firm_id'] != str(self.scope.firm_id) or frozen['enrollment_id'] != str(self.scope.enrollment_id)
                    or frozen['intake_id'] != str(revision.intake_id) or frozen['revision_id'] != str(revision.revision_id)
                    or frozen['sha256'] != revision.sha256 or frozen['byte_length'] != revision.byte_length):
                raise AdmissionRejected('Admission evidence unavailable')
            request = PortalIntentRequest(revision.intake_id, frozen['generation_id'], frozen['operation_id'],
                reservation.evidence_id, (revision.reference(),), frozen['processing_version'], {})
            payload, digest, exact_manifest = canonical_request(request)
            if manifest != exact_manifest:
                raise AdmissionRejected('Exact manifest unavailable')
            if state == 'committed':
                receipt = self.journey.intents.commit_at_cursor(cursor, payload, digest, manifest, reconcile_only=True)
                if (receipt.job_id, receipt.attempt_id, receipt.outbox_id) != saved[5:]:
                    raise AdmissionRejected('Retained admission receipt unavailable')
            else:
                expected = frozen['expected_current_generation']
                self._expected(cursor, scope, revision.intake_id, expected)
                key = scope + (revision.intake_id,)
                cursor.execute('INSERT INTO law_app.intakes VALUES (%s,%s,%s,true) ON CONFLICT (firm_id,enrollment_id,intake_id) DO NOTHING', key)
                if expected is not None:
                    cursor.execute("""UPDATE law_app.portal_generations SET state='superseded'
                        WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s AND generation_id=%s AND state='ready' RETURNING generation_id""",
                        key + (expected,))
                    if cursor.fetchone() is None:
                        raise AdmissionRejected('Current ready generation unavailable')
                cursor.execute("INSERT INTO law_app.portal_generations VALUES (%s,%s,%s,%s,'ready',%s,%s)",
                    key + (request.generation_id, reservation.evidence_id, manifest))
                self.journey.revisions.register_at_cursor(cursor, request, revision)
                cursor.execute('UPDATE law_app.fictional_intake_heads SET current_generation_id=%s WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s',
                    (request.generation_id,) + key)
                receipt = self.journey.intents.commit_at_cursor(cursor, payload, digest, manifest)
                cursor.execute("""UPDATE law_app.fictional_admissions SET state='committed',job_id=%s,attempt_id=%s,outbox_id=%s
                    WHERE firm_id=%s AND enrollment_id=%s AND reservation_id=%s AND state='reserved' RETURNING reservation_id""",
                    (receipt.job_id, receipt.attempt_id, receipt.outbox_id) + scope + (reservation.reservation_id,))
                if cursor.fetchone() is None:
                    raise AdmissionRejected('Admission reservation unavailable')
        return receipt

    def _matching_history(self, cursor, request, digest, revision, *, mutate=False):
        scope = (self.scope.firm_id, self.scope.enrollment_id)
        lock = 'UPDATE' if mutate else 'SHARE'
        cursor.execute("""SELECT reservation_id,evidence_id,request_sha256,state,request_json,job_id,attempt_id,outbox_id,abandoned_at
            FROM law_app.fictional_admissions WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s AND operation_id=%s FOR """ + lock,
            scope + (request.intake_id, request.operation_id))
        row = cursor.fetchone()
        if (row is None or row[2] != digest or row[4]['actor_id'] != str(self.actor.actor_id)
                or row[4]['purpose'] != PURPOSE
                or hashlib.sha256(json.dumps(row[4],sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest() != digest):
            raise AdmissionRejected('Admission history unavailable')
        return row, AdmissionReservation(*row[:3], revision)

    def status(self, request, data):
        _, digest, _, revision = canonical_admission(self.actor, request, data)
        with self._transaction() as cursor:
            scope = (self.scope.firm_id, self.scope.enrollment_id)
            # Owner-bound history can be read for inactive enrollment, but this
            # shared lock authorizes neither processing nor access to results.
            cursor.execute('SELECT active FROM law_app.enrollments WHERE firm_id=%s AND enrollment_id=%s FOR SHARE', scope)
            if cursor.fetchone() is None:
                raise AdmissionRejected('Admission history unavailable')
            row, reservation = self._matching_history(cursor, request, digest, revision)
            cursor.execute('SELECT current_generation_id FROM law_app.fictional_intake_heads WHERE firm_id=%s AND enrollment_id=%s AND intake_id=%s FOR SHARE',scope + (request.intake_id,))
            head = cursor.fetchone()
            if head is None:
                raise AdmissionRejected('Admission history unavailable')
            matches = head[0] == (request.generation_id if row[3]=='committed' else request.expected_current_generation)
            state = 'stale_expected_head' if row[3]=='reserved' and not matches else row[3]
            receipt = PortalIntentReceipt(*row[5:8],reused=True) if row[3]=='committed' else None
        try:
            self.journey.originals.verified_read(revision)
            byte_state = 'verified'
        except OriginalUnavailable:
            byte_state = 'unavailable'
        return AdmissionStatus(state,row[3],reservation,head[0],matches,byte_state,receipt)

    def abandon(self, request, data):
        _, digest, _, revision = canonical_admission(self.actor,request,data)
        with self._transaction() as cursor:
            self._enrollment(cursor)
            row,reservation = self._matching_history(cursor,request,digest,revision,mutate=True)
            if row[3]=='committed':
                raise AdmissionRejected('Committed admission cannot be abandoned')
            if row[3]=='abandoned':
                result = AdmissionAbandonment(reservation,row[8],reused=True)
            else:
                cursor.execute("""UPDATE law_app.fictional_admissions SET state='abandoned',abandoned_at=clock_timestamp()
                    WHERE firm_id=%s AND enrollment_id=%s AND reservation_id=%s AND state='reserved'
                      AND job_id IS NULL AND attempt_id IS NULL AND outbox_id IS NULL RETURNING abandoned_at""",
                    (self.scope.firm_id,self.scope.enrollment_id,reservation.reservation_id))
                saved = cursor.fetchone()
                if saved is None:
                    raise AdmissionRejected('Reserved admission unavailable')
                result = AdmissionAbandonment(reservation,saved[0],reused=False)
        # Capacity derives from reserved-row count: repeat never decrements it
        # again. No original/staging inspection or deletion occurs here.
        return result
