"""Opt-in disposable fixture wiring; never selected by web/worker startup.

The connection factory is owned by an explicitly authorized local test setup.
No driver import/connection or schema application happens during composition.
"""
from dataclasses import dataclass
import re

from law_app.adapters.persistence.postgres.portal_intents import PostgresPortalIntents
from law_app.ports.portal_intents import FirmEnrollment


@dataclass(frozen=True)
class FictionalPostgresProfile:
    enabled: bool = False
    disposable: bool = False
    host: str = "127.0.0.1"
    database: str = ""


def _fictional_connection(profile, connection_factory):
    if not profile.enabled:
        return None
    if (not profile.disposable or profile.host != "127.0.0.1"
            or not re.fullmatch(r"law_app_fictional_[0-9a-f]{32}", profile.database)):
        raise ValueError("Only an explicitly disposable local fictional database is supported")
    return lambda: connection_factory(host=profile.host, database=profile.database)


def build_fictional_portal_intents(profile: FictionalPostgresProfile, scope: FirmEnrollment,
                                 *, connection_factory):
    connect = _fictional_connection(profile, connection_factory)
    return PostgresPortalIntents(scope, connect) if connect is not None else None


def build_fictional_synthetic_work(profile: FictionalPostgresProfile, scope: FirmEnrollment,
                                  *, connection_factory):
    from law_app.adapters.persistence.postgres.synthetic_work import PostgresSyntheticWork
    connect = _fictional_connection(profile, connection_factory)
    return PostgresSyntheticWork(scope, connect) if connect is not None else None


class FictionalOriginalJourney:
    """Single-original fixture flow, with no installed routing or provider call."""
    def __init__(self, scope, connect, originals):
        from law_app.adapters.persistence.postgres.original_revisions import PostgresOriginalRevisions
        from law_app.adapters.persistence.postgres.synthetic_work import PostgresSyntheticWork
        self.originals = originals
        self.revisions = PostgresOriginalRevisions(scope, connect, originals)
        self.intents = PostgresPortalIntents(scope, connect)
        self.work = PostgresSyntheticWork(scope, connect)

    def publish_intent(self, intake_id, revision_id, data, *, generation_id, operation_id, authority_evidence_id):
        from law_app.ports.portal_intents import PortalIntentRequest
        revision = self.originals.publish(intake_id, revision_id, data)
        request = PortalIntentRequest(intake_id, generation_id, operation_id,
            authority_evidence_id, (revision.reference(),), 'fictional-original-v1', {})
        self.revisions.register(request, revision)
        return self.intents.commit_portal_intent(request)

    def process(self, job_id, owner_id, *, lease_seconds=30):
        import hashlib
        claim = self.work.claim(job_id, owner_id, lease_seconds)
        if claim is None:
            return None
        revision = self.revisions.committed_original(job_id)
        data = self.originals.verified_read(revision)
        claim = self.work.renew(claim, lease_seconds)
        claim = self.work.progress(claim, {'step': 'verified_original', 'bytes': len(data)})
        # Verify again before completing; corruption cannot yield a receipt.
        self.originals.verified_read(revision)
        return self.work.complete(claim, {'synthetic': True, 'review_required': True,
            'job_id': str(job_id), 'input_sha256': hashlib.sha256(data).hexdigest(),
            'input_byte_length': len(data)})

    def read_review_receipt(self, job_id):
        return self.revisions.read_review_receipt(job_id)

    def delivery_handler(self, worker_owner_id):
        def handle(claim):
            from law_app.ports.synthetic_work import ClaimRejected
            if claim.scope != self.work.scope:
                raise ClaimRejected("Delivery unavailable")
            self.process(claim.job_id, worker_owner_id)
            # Completed or duplicate deliveries reconcile through the scoped
            # current receipt reader; busy unfinished work cannot be acked here.
            return self.read_review_receipt(claim.job_id)
        return handle


def build_fictional_original_journey(profile, scope, *, connection_factory, originals):
    connect = _fictional_connection(profile, connection_factory)
    return FictionalOriginalJourney(scope, connect, originals) if connect is not None else None


def build_fictional_outbox_delivery(profile, scope, *, connection_factory):
    from law_app.adapters.persistence.postgres.outbox_delivery import PostgresOutboxDelivery
    connect = _fictional_connection(profile, connection_factory)
    return PostgresOutboxDelivery(scope, connect) if connect is not None else None
