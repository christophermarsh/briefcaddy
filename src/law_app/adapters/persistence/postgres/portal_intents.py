"""PostgreSQL portal intent repository, disconnected from installed workers.

Caller supplies fresh Psycopg3 connections; this operation owns its transaction.
No schema application, credentials, dispatch or provider execution occurs here.
"""
import json
from uuid import UUID, uuid4

from law_app.ports.portal_intents import (
    FirmEnrollment, IntentConflict, IntentUnavailable, IntentPersistenceFailure, PortalIntentReceipt,
    PortalIntentRequest, canonical_request,
)


class PostgresPortalIntents:
    def __init__(self, scope: FirmEnrollment, connection_factory):
        if not isinstance(scope.firm_id, UUID) or not isinstance(scope.enrollment_id, UUID):
            raise ValueError("Trusted composition must bind firm and enrollment UUIDs")
        self.scope = scope
        self._connect = connection_factory

    def commit_portal_intent(self, request: PortalIntentRequest) -> PortalIntentReceipt:
        # Freeze caller-owned options/refs before I/O; all later SQL uses this snapshot.
        payload, digest, manifest = canonical_request(request)
        try:
            return self._commit_snapshot(payload, digest, manifest)
        except (IntentConflict, IntentUnavailable, ValueError):
            raise
        except Exception:
            # No driver diagnostics/connection details cross the application port.
            # A lost commit response remains uncertain; reuse the same key after
            # reconciliation, never presume rollback from this generic outcome.
            raise IntentPersistenceFailure("Portal intent commit could not be confirmed") from None

    def reconcile_portal_intent(self, request: PortalIntentRequest) -> PortalIntentReceipt:
        """Read retained IDs after an uncertain response; never reconstruct work.

        Lack of current access/evidence leaves the outcome unknown, not rolled back.
        """
        payload, digest, manifest = canonical_request(request)
        try:
            return self._commit_snapshot(payload, digest, manifest, reconcile_only=True)
        except (IntentConflict, ValueError):
            raise
        except Exception:
            raise IntentPersistenceFailure("Portal intent outcome remains unconfirmed") from None

    def _retained(self, cursor, key, digest):
        cursor.execute("""SELECT job_id,attempt_id,outbox_id,digest_version,request_sha256
            FROM law_app.job_intents WHERE firm_id=%s AND enrollment_id=%s
            AND intake_id=%s AND generation_id=%s AND operation_id=%s""", key)
        retained = cursor.fetchone()
        if retained is None:
            raise IntentUnavailable("Portal operation unavailable")
        if retained[3:] != (1, digest):
            raise IntentConflict("Portal operation inputs changed")
        return PortalIntentReceipt(*retained[:3], reused=True)

    def _commit_snapshot(self, payload, digest, manifest, *, reconcile_only=False):
        with self._connect() as conn:
            if not conn.autocommit or conn.info.transaction_status != 0:
                raise ValueError("Intent repository requires a fresh idle autocommit connection")
            with conn.transaction(), conn.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cursor.execute("SELECT max(version) FROM law_app.schema_versions")
                if cursor.fetchone() not in ((1,), (2,), (3,), (4,), (5,)):
                    raise ValueError("Unsupported portal intent schema version")
                result = self.commit_at_cursor(cursor, payload, digest, manifest, reconcile_only=reconcile_only)
        return result

    def commit_at_cursor(self, cursor, payload, digest, manifest, *, reconcile_only=False):
        """Internal composition seam: caller owns transaction and commit result."""
        frozen = json.loads(payload)
        firm, enrollment = self.scope.firm_id, self.scope.enrollment_id
        intake = UUID(frozen["intake_id"])
        generation, operation = frozen["generation_id"], frozen["operation_id"]
        authority = UUID(frozen["authority_evidence_id"])
        refs = frozen["revisions"]
        key = (firm, enrollment, intake, generation, operation)
        lock_portal_inputs(cursor, self.scope, frozen, manifest)
        if reconcile_only:
            return self._retained(cursor, key, digest)
        job_id, attempt_id, outbox_id = uuid4(), uuid4(), uuid4()
        cursor.execute("""
            INSERT INTO law_app.job_intents
            (firm_id,enrollment_id,job_id,intake_id,generation_id,operation_id,
             attempt_id,outbox_id,digest_version,request_sha256,request_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s::jsonb)
            ON CONFLICT (firm_id,enrollment_id,intake_id,generation_id,operation_id)
            DO NOTHING RETURNING job_id
        """, (firm, enrollment, job_id, intake, generation, operation,
              attempt_id, outbox_id, digest, payload))
        if cursor.fetchone() is None:
            # READ COMMITTED sees the winner after ON CONFLICT waits. No
            # replacement, new attempt, outbox or recovery reconstruction.
            result = self._retained(cursor, key, digest)
        else:
            cursor.execute("""
                INSERT INTO law_app.processing_attempts (firm_id,enrollment_id,attempt_id,job_id)
                VALUES (%s,%s,%s,%s)
            """, (firm, enrollment, attempt_id, job_id))
            for ref in refs:
                cursor.execute("""
                    INSERT INTO law_app.job_revisions
                    (firm_id,enrollment_id,job_id,intake_id,generation_id,revision_id,sha256,byte_length)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (firm, enrollment, job_id, intake, generation,
                      UUID(ref["revision_id"]), ref["sha256"], ref["byte_length"]))
            cursor.execute("""
                INSERT INTO law_app.outbox_intents (firm_id,enrollment_id,outbox_id,job_id)
                VALUES (%s,%s,%s,%s)
            """, (firm, enrollment, outbox_id, job_id))
            result = PortalIntentReceipt(job_id, attempt_id, outbox_id, reused=False)
        return result


def lock_portal_inputs(cursor, scope, frozen, manifest):
    """Shared fictional eligibility floor for intent and synthetic work transactions."""
    firm, enrollment = scope.firm_id, scope.enrollment_id
    intake, authority = UUID(frozen["intake_id"]), UUID(frozen["authority_evidence_id"])
    generation, refs = frozen["generation_id"], frozen["revisions"]
    # Shared row locks serialize current enrollment/intake/generation
    # revocation against this short publication transaction.
    cursor.execute("""
        SELECT g.generation_id FROM law_app.portal_generations g
        JOIN law_app.intakes i USING (firm_id, enrollment_id, intake_id)
        JOIN law_app.enrollments e USING (firm_id, enrollment_id)
        WHERE g.firm_id=%s AND g.enrollment_id=%s AND g.intake_id=%s
          AND g.generation_id=%s AND e.active AND i.open AND g.state='ready'
          AND g.authority_evidence_id=%s AND g.revision_manifest_sha256=%s
        FOR SHARE OF e, i, g
    """, (firm, enrollment, intake, generation, authority, manifest))
    if cursor.fetchone() is None:
        raise IntentUnavailable("Portal inputs unavailable")
    revision_ids = [UUID(ref["revision_id"]) for ref in refs]
    cursor.execute("""
        SELECT r.revision_id, r.sha256, r.byte_length
        FROM law_app.generation_revisions gr
        JOIN law_app.revisions r USING (firm_id,enrollment_id,intake_id,revision_id,sha256,byte_length)
        WHERE gr.firm_id=%s AND gr.enrollment_id=%s AND gr.intake_id=%s AND gr.generation_id=%s
          AND r.revision_id=ANY(%s::uuid[]) AND r.state='readable'
        FOR SHARE OF gr, r
    """, (firm, enrollment, intake, generation, revision_ids))
    current = {str(row[0]): (row[1], row[2]) for row in cursor.fetchall()}
    expected = {ref["revision_id"]: (ref["sha256"], ref["byte_length"]) for ref in refs}
    if current != expected:
        raise IntentUnavailable("Portal inputs unavailable")
