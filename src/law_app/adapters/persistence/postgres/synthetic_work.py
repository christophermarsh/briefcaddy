"""Database-time synthetic work leases; no installed worker or provider effects."""
from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID, uuid4

from law_app.adapters.persistence.postgres.portal_intents import lock_portal_inputs
from law_app.ports.portal_intents import IntentUnavailable, PortalIntentRequest, RevisionReference, canonical_request
from law_app.ports.synthetic_work import (
    ClaimRejected, CompletionReceipt, WorkClaim, WorkPersistenceFailure,
    canonical_metadata, duration, validate_claim,
)


class PostgresSyntheticWork:
    def __init__(self, scope, connection_factory):
        if not isinstance(scope.firm_id, UUID) or not isinstance(scope.enrollment_id, UUID):
            raise ValueError("Trusted firm/enrollment UUIDs required")
        self.scope, self._connect = scope, connection_factory

    @contextmanager
    def _cursor(self):
        try:
            with self._connect() as conn:
                if not conn.autocommit or conn.info.transaction_status != 0:
                    raise ValueError("Fresh idle autocommit connection required")
                with conn.transaction(), conn.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                    cursor.execute("SELECT max(version) FROM law_app.schema_versions")
                    if cursor.fetchone() not in ((2,), (3,), (4,), (5,)):
                        raise ValueError("Synthetic work requires supported schema v2/v3/v4/v5")
                    yield cursor
        except IntentUnavailable:
            raise ClaimRejected("Synthetic work unavailable") from None
        except (ClaimRejected, ValueError):
            raise
        except Exception:
            raise WorkPersistenceFailure("Synthetic work commit could not be confirmed") from None

    def _key(self, job_id):
        if not isinstance(job_id, UUID):
            raise ValueError("Stable job UUID required")
        return self.scope.firm_id, self.scope.enrollment_id, job_id

    def _locked_job(self, cursor, job_id):
        key = self._key(job_id)
        cursor.execute("SELECT request_json,request_sha256 FROM law_app.job_intents WHERE firm_id=%s AND enrollment_id=%s AND job_id=%s", key)
        saved = cursor.fetchone()
        if saved is None:
            raise ClaimRejected("Synthetic work unavailable")
        frozen, request_digest = saved
        try:
            request = PortalIntentRequest(UUID(frozen['intake_id']), frozen['generation_id'], frozen['operation_id'],
                UUID(frozen['authority_evidence_id']), tuple(RevisionReference(UUID(r['revision_id']), r['sha256'], r['byte_length'])
                for r in frozen['revisions']), frozen['processing_version'], frozen['options'])
            _, _, manifest = canonical_request(request)
        except (KeyError, TypeError, ValueError):
            raise ClaimRejected("Synthetic work unavailable") from None
        # JSONB normalizes numeric representations; retain the original request
        # digest rather than pretending JSONB can reconstruct its exact text.
        lock_portal_inputs(cursor, self.scope, frozen, manifest)
        cursor.execute("""
            SELECT attempt_id,state,state_version,fence,current_execution_id,
                   not_before,request_sha256,not_before IS NULL OR not_before<=clock_timestamp()
            FROM law_app.job_intents WHERE firm_id=%s AND enrollment_id=%s AND job_id=%s FOR UPDATE
        """, key)
        current = cursor.fetchone()
        if current is None or current[6] != request_digest:
            raise ClaimRejected("Synthetic work unavailable")
        owner, expires, live = None, None, False
        if current[4] is not None:
            # Separate statement after the exclusive job lock sees a concurrent
            # winner's committed attempt; no stale outer-join snapshot takeover.
            cursor.execute("SELECT owner_id,expires_at,expires_at>clock_timestamp() FROM law_app.work_attempts WHERE firm_id=%s AND enrollment_id=%s AND execution_id=%s AND job_id=%s",
                           key[:2] + (current[4], job_id))
            attempt = cursor.fetchone()
            if attempt is None:
                raise ClaimRejected("Synthetic work unavailable")
            owner, expires, live = attempt
        row = current[:5] + (owner, expires, current[5], current[6], live, current[7])
        return row, frozen

    def _token(self, claim, row, *, completed=False):
        expected_version = claim.state_version + (1 if completed else 0)
        if (row[0] != claim.bound_attempt_id or row[1] != ('done' if completed else 'running')
                or row[2] != expected_version or row[3] != claim.fence
                or row[4] != claim.execution_id or row[5] != claim.owner_id or not row[9]):
            raise ClaimRejected("Synthetic work unavailable")

    def _cas(self, cursor, claim, assignments, params=()):
        # Only internal constant SQL fragments enter assignments. Clock check is
        # at the conditional mutation, not at transaction start/caller time.
        cursor.execute("""UPDATE law_app.job_intents j SET """ + assignments + """,
                state_version=state_version+1
            WHERE j.firm_id=%s AND j.enrollment_id=%s AND j.job_id=%s
              AND j.state='running' AND j.state_version=%s AND j.fence=%s
              AND j.current_execution_id=%s AND j.attempt_id=%s
              AND EXISTS (SELECT 1 FROM law_app.work_attempts w
                WHERE w.firm_id=j.firm_id AND w.enrollment_id=j.enrollment_id
                  AND w.execution_id=j.current_execution_id AND w.job_id=j.job_id
                  AND w.owner_id=%s AND w.fence=%s AND w.expires_at>clock_timestamp())
            RETURNING state_version""", tuple(params) + self._key(claim.job_id) +
            (claim.state_version, claim.fence, claim.execution_id, claim.bound_attempt_id, claim.owner_id, claim.fence))
        row = cursor.fetchone()
        if row is None:
            raise ClaimRejected("Synthetic work unavailable")
        return row[0]

    def claim(self, job_id, owner_id, lease_seconds):
        duration(lease_seconds)
        if not isinstance(owner_id, UUID):
            raise ValueError("Stable owner UUID required")
        key = self._key(job_id)
        with self._cursor() as cursor:
            row, _ = self._locked_job(cursor, job_id)
            if row[1] == 'done' or row[1] == 'running' and row[9] or row[1] == 'retry' and not row[10]:
                return None
            if row[1] not in ('intent','retry','running'):
                raise ClaimRejected("Synthetic work unavailable")
            if row[4] is not None:
                cursor.execute("UPDATE law_app.work_attempts SET state='expired' WHERE firm_id=%s AND enrollment_id=%s AND execution_id=%s", key[:2] + (row[4],))
            execution, fence = uuid4(), row[3] + 1
            cursor.execute("""INSERT INTO law_app.work_attempts
                (firm_id,enrollment_id,execution_id,job_id,bound_attempt_id,owner_id,fence,expires_at,state)
                VALUES (%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+make_interval(secs=>%s),'running')
                RETURNING expires_at""", key[:2] + (execution, job_id, row[0], owner_id, fence, lease_seconds))
            expires = cursor.fetchone()[0]
            cursor.execute("""UPDATE law_app.job_intents SET state='running',state_version=state_version+1,
                fence=%s,current_execution_id=%s,not_before=NULL
                WHERE firm_id=%s AND enrollment_id=%s AND job_id=%s AND state_version=%s
                  AND (state='intent' OR (state='retry' AND (not_before IS NULL OR not_before<=clock_timestamp()))
                    OR (state='running' AND EXISTS (SELECT 1 FROM law_app.work_attempts w
                      WHERE w.firm_id=job_intents.firm_id AND w.enrollment_id=job_intents.enrollment_id
                        AND w.execution_id=job_intents.current_execution_id AND w.expires_at<=clock_timestamp())))
                  AND EXISTS (SELECT 1 FROM law_app.work_attempts w WHERE w.firm_id=job_intents.firm_id
                    AND w.enrollment_id=job_intents.enrollment_id AND w.execution_id=%s AND w.expires_at>clock_timestamp())
                RETURNING state_version""", (fence, execution) + key + (row[2], execution))
            saved = cursor.fetchone()
            if saved is None:
                raise ClaimRejected("Synthetic work unavailable")
            version = saved[0]
            result = WorkClaim(self.scope, job_id, row[0], execution, owner_id, fence, version, expires)
        return result

    def renew(self, claim, lease_seconds):
        validate_claim(claim, self.scope)
        duration(lease_seconds)
        with self._cursor() as cursor:
            row, _ = self._locked_job(cursor, claim.job_id)
            self._token(claim, row)
            version = self._cas(cursor, claim, "state='running'")
            cursor.execute("""UPDATE law_app.work_attempts SET expires_at=clock_timestamp()+make_interval(secs=>%s)
                WHERE firm_id=%s AND enrollment_id=%s AND execution_id=%s AND owner_id=%s AND fence=%s
                  AND expires_at>clock_timestamp() RETURNING expires_at""",
                (lease_seconds, self.scope.firm_id, self.scope.enrollment_id, claim.execution_id, claim.owner_id, claim.fence))
            saved = cursor.fetchone()
            if saved is None:
                raise ClaimRejected("Synthetic work unavailable")
            result = replace(claim, state_version=version, expires_at=saved[0])
        return result

    def progress(self, claim, value):
        validate_claim(claim, self.scope)
        text, _ = canonical_metadata(value)
        with self._cursor() as cursor:
            row, _ = self._locked_job(cursor, claim.job_id)
            self._token(claim, row)
            version = self._cas(cursor, claim, "state='running'")
            cursor.execute("""INSERT INTO law_app.work_progress (firm_id,enrollment_id,job_id,execution_id,state_version,value)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (firm_id,enrollment_id,job_id)
                DO UPDATE SET execution_id=EXCLUDED.execution_id,state_version=EXCLUDED.state_version,value=EXCLUDED.value""",
                self._key(claim.job_id) + (claim.execution_id, version, text))
            result = replace(claim, state_version=version)
        return result

    def retry(self, claim, delay_seconds):
        validate_claim(claim, self.scope)
        duration(delay_seconds, allow_zero=True)
        with self._cursor() as cursor:
            row, _ = self._locked_job(cursor, claim.job_id)
            self._token(claim, row)
            cursor.execute("UPDATE law_app.work_attempts SET state='retry' WHERE firm_id=%s AND enrollment_id=%s AND execution_id=%s", self._key(claim.job_id)[:2] + (claim.execution_id,))
            version = self._cas(cursor, claim, "state='retry',current_execution_id=NULL,not_before=clock_timestamp()+make_interval(secs=>%s)", (delay_seconds,))
        return version

    def complete(self, claim, result):
        validate_claim(claim, self.scope)
        if (type(result) is not dict or result.get('synthetic') is not True
                or result.get('review_required') is not True or result.get('job_id') != str(claim.job_id)):
            raise ValueError("Only synthetic results retaining human review are supported")
        text, digest = canonical_metadata(result)
        with self._cursor() as cursor:
            row, frozen = self._locked_job(cursor, claim.job_id)
            if row[1] == 'done':
                self._token(claim, row, completed=True)
                cursor.execute("""SELECT p.result_id,p.receipt_id,p.completion_outbox_id,r.result_sha256,p.claim_state_version
                    FROM law_app.processed_receipts p JOIN law_app.processing_results r
                      USING (firm_id,enrollment_id,result_id,job_id,execution_id,owner_id,fence)
                    WHERE p.firm_id=%s AND p.enrollment_id=%s AND p.job_id=%s""", self._key(claim.job_id))
                saved = cursor.fetchone()
                if saved is None or saved[3:] != (digest, claim.state_version):
                    raise ClaimRejected("Synthetic work unavailable")
                result_receipt = CompletionReceipt(*saved[:3], reused=True)
            else:
                self._token(claim, row)
                result_id, receipt_id, outbox_id = uuid4(), uuid4(), uuid4()
                cursor.execute("""INSERT INTO law_app.processing_results
                    (firm_id,enrollment_id,result_id,job_id,execution_id,owner_id,fence,result_sha256,result_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                    self._key(claim.job_id)[:2] + (result_id, claim.job_id, claim.execution_id, claim.owner_id, claim.fence, digest, text))
                cursor.execute("""INSERT INTO law_app.processed_receipts
                    (firm_id,enrollment_id,receipt_id,job_id,intake_id,generation_id,bound_attempt_id,execution_id,
                     owner_id,fence,claim_state_version,result_id,completion_outbox_id,request_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    self._key(claim.job_id)[:2] + (receipt_id, claim.job_id, UUID(frozen['intake_id']), frozen['generation_id'],
                    claim.bound_attempt_id, claim.execution_id, claim.owner_id, claim.fence, claim.state_version, result_id, outbox_id, row[8]))
                cursor.execute("""INSERT INTO law_app.outbox_intents
                    (firm_id,enrollment_id,outbox_id,job_id,event_kind,processed_receipt_id)
                    VALUES (%s,%s,%s,%s,'portal.processed.v1',%s)""", self._key(claim.job_id)[:2] + (outbox_id, claim.job_id, receipt_id))
                cursor.execute("UPDATE law_app.work_attempts SET state='completed' WHERE firm_id=%s AND enrollment_id=%s AND execution_id=%s", self._key(claim.job_id)[:2] + (claim.execution_id,))
                self._cas(cursor, claim, "state='done'")
                result_receipt = CompletionReceipt(result_id, receipt_id, outbox_id, reused=False)
        return result_receipt
