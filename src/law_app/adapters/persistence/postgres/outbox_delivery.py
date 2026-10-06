"""Scoped fictional delivery claims; network/broker transport intentionally absent."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from law_app.adapters.persistence.postgres.synthetic_work import PostgresSyntheticWork
from law_app.ports.portal_intents import FirmEnrollment
from law_app.ports.synthetic_work import ClaimRejected, duration


@dataclass(frozen=True)
class DeliveryClaim:
    scope: FirmEnrollment
    outbox_id: UUID
    job_id: UUID
    delivery_id: UUID
    owner_id: UUID
    fence: int
    expires_at: datetime


class PostgresOutboxDelivery:
    SCAN_LIMIT = 16

    def __init__(self, scope, connection_factory):
        self.scope = scope
        self.work = PostgresSyntheticWork(scope, connection_factory)

    def _key(self, outbox_id):
        if not isinstance(outbox_id, UUID):
            raise ValueError("Stable outbox UUID required")
        return self.scope.firm_id, self.scope.enrollment_id, outbox_id

    def _locked(self, cursor, outbox_id):
        self._schema(cursor)
        key = self._key(outbox_id)
        cursor.execute("""SELECT job_id FROM law_app.outbox_intents
            WHERE firm_id=%s AND enrollment_id=%s AND outbox_id=%s AND event_kind='portal.job_intent.v1'""", key)
        saved = cursor.fetchone()
        if saved is None:
            raise ClaimRejected("Delivery unavailable")
        # Always acquire scoped eligibility/job before the outbox lock. The
        # separate statement after locking sees a competing winner's token.
        self.work._locked_job(cursor, saved[0])
        cursor.execute("""SELECT job_id,state,delivery_id,delivery_owner_id,delivery_fence,
            delivery_expires_at,delivery_expires_at>clock_timestamp()
            FROM law_app.outbox_intents WHERE firm_id=%s AND enrollment_id=%s AND outbox_id=%s
              AND event_kind='portal.job_intent.v1' FOR UPDATE""", key)
        row = cursor.fetchone()
        if row is None:
            raise ClaimRejected("Delivery unavailable")
        return row

    @staticmethod
    def _schema(cursor):
        cursor.execute('SELECT max(version) FROM law_app.schema_versions')
        if cursor.fetchone() not in ((3,), (4,), (5,)):
            raise ValueError("Fictional outbox delivery requires supported schema v3/v4/v5")

    def pending_ids(self, *, scan_limit=SCAN_LIMIT):
        """Bounded deterministic hints, not authority or a whole-queue view."""
        if type(scan_limit) is not int or not 1 <= scan_limit <= self.SCAN_LIMIT:
            raise ValueError('Bounded fictional scan limit required')
        with self.work._cursor() as cursor:
            self._schema(cursor)
            # No FOR UPDATE: selection never takes the outbox lock first.
            cursor.execute("""SELECT outbox_id FROM law_app.outbox_intents
                WHERE firm_id=%s AND enrollment_id=%s AND event_kind='portal.job_intent.v1'
                  AND state='pending'
                  AND (delivery_expires_at IS NULL OR delivery_expires_at<=clock_timestamp())
                ORDER BY created_at,outbox_id LIMIT %s""",
                (self.scope.firm_id, self.scope.enrollment_id, scan_limit))
            result = tuple(row[0] for row in cursor.fetchall())
        return result

    def claim_next(self, owner_id, lease_seconds, *, scan_limit=SCAN_LIMIT):
        duration(lease_seconds)
        if not isinstance(owner_id, UUID):
            raise ValueError('Stable delivery owner UUID required')
        for outbox_id in self.pending_ids(scan_limit=scan_limit):
            try:
                claim = self.claim(outbox_id, owner_id, lease_seconds)
            except ClaimRejected:
                # Only explicit current-input/token rejection is skippable.
                # Unknown persistence outcomes/configuration errors propagate.
                continue
            if claim is not None:
                return claim
        return None

    def claim(self, outbox_id, owner_id, lease_seconds):
        duration(lease_seconds)
        if not isinstance(owner_id, UUID):
            raise ValueError("Stable delivery owner UUID required")
        key = self._key(outbox_id)
        with self.work._cursor() as cursor:
            row = self._locked(cursor, outbox_id)
            if row[1] == 'delivered' or row[6]:
                return None
            delivery_id = uuid4()
            cursor.execute("""UPDATE law_app.outbox_intents SET delivery_id=%s,delivery_owner_id=%s,
                delivery_fence=delivery_fence+1,delivery_expires_at=clock_timestamp()+make_interval(secs=>%s)
                WHERE firm_id=%s AND enrollment_id=%s AND outbox_id=%s AND event_kind='portal.job_intent.v1'
                  AND state='pending' AND delivery_fence=%s
                  AND (delivery_expires_at IS NULL OR delivery_expires_at<=clock_timestamp())
                RETURNING delivery_fence,delivery_expires_at""", (delivery_id, owner_id, lease_seconds) + key + (row[4],))
            saved = cursor.fetchone()
            if saved is None:
                raise ClaimRejected("Delivery unavailable")
            claim = DeliveryClaim(self.scope, outbox_id, row[0], delivery_id, owner_id, *saved)
        return claim

    def acknowledge(self, claim):
        if claim.scope != self.scope:
            raise ClaimRejected("Delivery unavailable")
        if (any(not isinstance(value, UUID) for value in
                (claim.outbox_id, claim.job_id, claim.delivery_id, claim.owner_id))
                or type(claim.fence) is not int or claim.fence < 1):
            raise ValueError("Invalid delivery token")
        with self.work._cursor() as cursor:
            self._locked(cursor, claim.outbox_id)
            cursor.execute("""UPDATE law_app.outbox_intents SET state='delivered',delivered_at=clock_timestamp()
                WHERE firm_id=%s AND enrollment_id=%s AND outbox_id=%s AND job_id=%s
                  AND event_kind='portal.job_intent.v1' AND state='pending'
                  AND delivery_id=%s AND delivery_owner_id=%s AND delivery_fence=%s
                  AND delivery_expires_at>clock_timestamp() RETURNING outbox_id""",
                self._key(claim.outbox_id) + (claim.job_id, claim.delivery_id, claim.owner_id, claim.fence))
            if cursor.fetchone() is None:
                raise ClaimRejected("Delivery unavailable")


def deliver_fictional_once(delivery, outbox_id, owner_id, handler, *, lease_seconds=30):
    """Injected local callable runs after claim commit and before a separate ack.

    No automatic retry/sleep. A handler/ack failure leaves the delivery pending;
    later expiry/redelivery preserves its message and job IDs.
    """
    claim = (delivery.claim_next(owner_id, lease_seconds) if outbox_id is None
             else delivery.claim(outbox_id, owner_id, lease_seconds))
    if claim is None:
        return None
    result = handler(claim)
    delivery.acknowledge(claim)
    return result
