"""Runnable fictional intake service, not production authentication/policy."""
from dataclasses import dataclass
from uuid import UUID

from law_app.ports.portal_intents import FirmEnrollment
from law_app.ports.synthetic_work import ClaimRejected


@dataclass(frozen=True)
class FictionalActor:
    scope: FirmEnrollment
    actor_id: UUID
    role: str

    def __post_init__(self):
        if (not isinstance(self.actor_id, UUID) or self.role not in ('service', 'worker')
                or not isinstance(self.scope.firm_id, UUID) or not isinstance(self.scope.enrollment_id, UUID)):
            raise ValueError('Explicit fictional actor/scope/role required')


class FictionalIntakeService:
    def __init__(self, actor, journey, admissions=None):
        if actor.scope != journey.work.scope:
            raise ValueError('Trusted actor and journey scope must match')
        self.actor, self.journey, self.admissions = actor, journey, admissions

    def _service(self):
        if self.actor.role != 'service':
            raise ClaimRejected('Fictional service unavailable')

    def upload(self, intake_id, revision_id, data, *, generation_id, operation_id, expected_current_generation):
        self._service()
        from law_app.ports.fictional_admissions import AdmissionRequest
        if self.admissions is None:
            raise ValueError('Explicit fictional admission repository required')
        request = AdmissionRequest(intake_id, revision_id, generation_id, operation_id, expected_current_generation)
        reservation = self.admissions.reserve(request, data)
        revision = self.journey.originals.publish(intake_id, revision_id, data)
        return self.admissions.finish(reservation, revision)

    def status(self, job_id):
        self._service()
        state = self.journey.revisions.read_status(job_id)
        if state == 'done':
            # A done metadata row alone is insufficient to expose review success.
            self.journey.read_review_receipt(job_id)
        names = {'intent': 'queued', 'running': 'processing', 'retry': 'retry_pending', 'done': 'review_required'}
        return {'job_id': str(job_id), 'status': names[state], 'review_required': state == 'done'}

    def review(self, job_id):
        self._service()
        receipt, result = self.journey.read_review_receipt(job_id)
        return {'job_id': str(job_id), 'result_id': str(receipt.result_id),
            'receipt_id': str(receipt.receipt_id), 'outbox_id': str(receipt.outbox_id), 'result': result}

    def admission_status(self, request, data):
        self._service()
        if self.admissions is None:
            raise ValueError('Explicit fictional admission repository required')
        return self.admissions.status(request,data)

    def abandon(self, request, data):
        self._service()
        if self.admissions is None:
            raise ValueError('Explicit fictional admission repository required')
        return self.admissions.abandon(request,data)
