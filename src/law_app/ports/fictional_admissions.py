"""Fictional admission only; issued evidence is not production consent."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from uuid import UUID

from law_app.ports.originals import OriginalRevision, original_locator
from law_app.ports.portal_intents import PortalIntentReceipt, PortalIntentRequest, canonical_request
from law_app.ports.synthetic_work import ClaimRejected, WorkPersistenceFailure

PURPOSE = 'fictional_intake_upload'
MAX_BYTES = 16 * 1024 * 1024


class AdmissionConflict(ValueError):
    pass


class AdmissionRejected(ClaimRejected):
    pass


class AdmissionCapacity(AdmissionRejected):
    pass


class AdmissionPersistenceFailure(WorkPersistenceFailure):
    """Unconfirmed outcome; retain keys/reservation/bytes for reconciliation."""


@dataclass(frozen=True)
class AdmissionRequest:
    intake_id: UUID
    revision_id: UUID
    generation_id: str
    operation_id: str
    expected_current_generation: str | None


@dataclass(frozen=True)
class AdmissionReservation:
    reservation_id: UUID
    evidence_id: UUID
    request_sha256: str
    revision: OriginalRevision


@dataclass(frozen=True)
class AdmissionStatus:
    state: str
    lifecycle: str
    reservation: AdmissionReservation
    current_generation: str | None
    head_matches: bool
    original_bytes: str
    receipt: PortalIntentReceipt | None
    historical_only: bool = True


@dataclass(frozen=True)
class AdmissionAbandonment:
    reservation: AdmissionReservation
    abandoned_at: datetime
    reused: bool


def canonical_admission(actor, request, data):
    if actor.role != 'service' or type(data) is not bytes or len(data) > MAX_BYTES:
        raise ValueError('Fictional service and bounded bytes required')
    if not isinstance(request.intake_id, UUID) or not isinstance(request.revision_id, UUID):
        raise ValueError('Stable intake/revision UUIDs required')
    expected = request.expected_current_generation
    if expected is not None and (not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{32,64}', expected)):
        raise ValueError('Exact expected generation required')
    if expected == request.generation_id:
        raise ValueError('Replacement must have a distinct generation')
    revision = OriginalRevision(actor.scope, request.intake_id, request.revision_id,
        hashlib.sha256(data).hexdigest(), len(data), original_locator(actor.scope, request.intake_id, request.revision_id))
    # Reuse the existing manifest encoding. The placeholder is only a hash input;
    # actual authority evidence is issued by the reservation, never the request.
    _, _, manifest = canonical_request(PortalIntentRequest(request.intake_id, request.generation_id,
        request.operation_id, UUID(int=0), (revision.reference(),), 'fictional-original-v1', {}))
    frozen = {'firm_id': str(actor.scope.firm_id), 'enrollment_id': str(actor.scope.enrollment_id),
        'actor_id': str(actor.actor_id), 'purpose': PURPOSE, 'intake_id': str(request.intake_id),
        'revision_id': str(request.revision_id), 'generation_id': request.generation_id,
        'operation_id': request.operation_id, 'expected_current_generation': expected,
        'sha256': revision.sha256, 'byte_length': revision.byte_length, 'processing_version': 'fictional-original-v1'}
    payload = json.dumps(frozen, sort_keys=True, separators=(',',':'), allow_nan=False)
    return payload, hashlib.sha256(payload.encode('utf-8')).hexdigest(), manifest, revision
