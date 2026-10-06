"""Fictional byte-backed revision registration and scoped review reads."""
import json
from uuid import UUID

from law_app.adapters.persistence.postgres.synthetic_work import PostgresSyntheticWork
from law_app.adapters.persistence.postgres.portal_intents import lock_portal_inputs
from law_app.ports.originals import OriginalConflict, OriginalRevision, OriginalUnavailable
from law_app.ports.portal_intents import canonical_request
from law_app.ports.synthetic_work import ClaimRejected, CompletionReceipt


class PostgresOriginalRevisions:
    def __init__(self, scope, connection_factory, originals):
        if originals.scope != scope:
            raise ValueError("Originals and repository must share trusted scope")
        self.scope, self.originals = scope, originals
        self.work = PostgresSyntheticWork(scope, connection_factory)

    def register(self, request, revision):
        self.originals.verified_read(revision)
        with self.work._cursor() as cursor:
            self.register_at_cursor(cursor, request, revision)
        return revision

    def register_at_cursor(self, cursor, request, revision):
        """Internal seam; caller verifies bytes before its owning transaction."""
        payload, _, manifest = canonical_request(request)
        if request.intake_id != revision.intake_id or request.revisions != (revision.reference(),):
            raise ValueError("Exact single-original request required")
        frozen = json.loads(payload)
        key = (self.scope.firm_id, self.scope.enrollment_id, revision.intake_id)
        cursor.execute("""SELECT g.generation_id FROM law_app.portal_generations g
            JOIN law_app.intakes i USING (firm_id,enrollment_id,intake_id)
            JOIN law_app.enrollments e USING (firm_id,enrollment_id)
            WHERE g.firm_id=%s AND g.enrollment_id=%s AND g.intake_id=%s
              AND g.generation_id=%s AND e.active AND i.open AND g.state='ready'
              AND g.authority_evidence_id=%s AND g.revision_manifest_sha256=%s
            FOR SHARE OF e,i,g""", key + (request.generation_id, request.authority_evidence_id, manifest))
        if cursor.fetchone() is None:
            raise ClaimRejected("Original registration unavailable")
        cursor.execute("""INSERT INTO law_app.revisions
            (firm_id,enrollment_id,intake_id,revision_id,sha256,byte_length,locator,state)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'readable')
            ON CONFLICT (firm_id,enrollment_id,revision_id) DO NOTHING""",
            key + (revision.revision_id, revision.sha256, revision.byte_length, revision.locator))
        cursor.execute("""SELECT intake_id,sha256,byte_length,locator,state FROM law_app.revisions
            WHERE firm_id=%s AND enrollment_id=%s AND revision_id=%s FOR SHARE""",
            key[:2] + (revision.revision_id,))
        if cursor.fetchone() != (revision.intake_id, revision.sha256, revision.byte_length, revision.locator, 'readable'):
            raise OriginalConflict("Immutable revision metadata conflicts")
        cursor.execute("""INSERT INTO law_app.generation_revisions
            (firm_id,enrollment_id,intake_id,generation_id,revision_id,sha256,byte_length)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (firm_id,enrollment_id,intake_id,generation_id,revision_id) DO NOTHING""",
            key + (request.generation_id, revision.revision_id, revision.sha256, revision.byte_length))
        lock_portal_inputs(cursor, self.scope, frozen, manifest)

    def committed_original(self, job_id):
        with self.work._cursor() as cursor:
            _, frozen = self.work._locked_job(cursor, job_id)
            refs = frozen['revisions']
            if len(refs) != 1:
                raise ClaimRejected("Single original unavailable")
            ref = refs[0]
            cursor.execute("""SELECT r.intake_id,r.revision_id,r.sha256,r.byte_length,r.locator
                FROM law_app.job_revisions j JOIN law_app.revisions r
                  USING (firm_id,enrollment_id,intake_id,revision_id,sha256,byte_length)
                WHERE j.firm_id=%s AND j.enrollment_id=%s AND j.job_id=%s
                  AND j.generation_id=%s AND j.revision_id=%s AND j.sha256=%s
                  AND j.byte_length=%s AND r.state='readable' FOR SHARE OF j,r""",
                self.work._key(job_id) + (frozen['generation_id'], UUID(ref['revision_id']), ref['sha256'], ref['byte_length']))
            row = cursor.fetchone()
            if row is None:
                raise ClaimRejected("Original unavailable")
            revision = OriginalRevision(self.scope, *row)
        self.originals.verified_read(revision)
        return revision

    def read_review_receipt(self, job_id):
        # This is a read with current scope/eligibility, not a completion replay
        # and not a lease-authorized mutation. Missing bytes refuse a review read.
        revision = self.committed_original(job_id)
        with self.work._cursor() as cursor:
            self.work._locked_job(cursor, job_id)
            cursor.execute("""SELECT p.result_id,p.receipt_id,p.completion_outbox_id,r.result_json
                FROM law_app.processed_receipts p JOIN law_app.processing_results r
                  USING (firm_id,enrollment_id,result_id,job_id,execution_id,owner_id,fence)
                JOIN law_app.job_intents j USING (firm_id,enrollment_id,job_id)
                WHERE p.firm_id=%s AND p.enrollment_id=%s AND p.job_id=%s AND j.state='done'""",
                self.work._key(job_id))
            row = cursor.fetchone()
            if row is None:
                raise ClaimRejected("Review receipt unavailable")
            result = row[3]
            if (result.get('synthetic') is not True or result.get('review_required') is not True
                    or result.get('job_id') != str(job_id) or result.get('input_sha256') != revision.sha256
                    or result.get('input_byte_length') != revision.byte_length):
                raise ClaimRejected("Review receipt unavailable")
            receipt = CompletionReceipt(*row[:3], reused=True)
        return receipt, result

    def read_status(self, job_id):
        with self.work._cursor() as cursor:
            row, _ = self.work._locked_job(cursor, job_id)
            return row[1]
