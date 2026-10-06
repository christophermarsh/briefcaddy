"""Fictional synthetic-work contract: no broker or installed worker routing."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID
import hashlib
import json

from law_app.ports.portal_intents import FirmEnrollment, _json_value


@dataclass(frozen=True)
class WorkClaim:
    scope: FirmEnrollment
    job_id: UUID
    bound_attempt_id: UUID
    execution_id: UUID
    owner_id: UUID
    fence: int
    state_version: int
    expires_at: datetime


@dataclass(frozen=True)
class CompletionReceipt:
    result_id: UUID
    receipt_id: UUID
    outbox_id: UUID
    reused: bool


class ClaimRejected(LookupError):
    """Unavailable scope/current inputs or stale/expired token; no replacement."""


class WorkPersistenceFailure(RuntimeError):
    """Outcome unknown: do not assume the transaction rolled back."""


class SyntheticWork(Protocol):
    def claim(self, job_id: UUID, owner_id: UUID, lease_seconds: int) -> WorkClaim | None: ...
    def renew(self, claim: WorkClaim, lease_seconds: int) -> WorkClaim: ...
    def progress(self, claim: WorkClaim, value: dict) -> WorkClaim: ...
    def retry(self, claim: WorkClaim, delay_seconds: int) -> int: ...
    def complete(self, claim: WorkClaim, result: dict) -> CompletionReceipt: ...


def duration(seconds, *, allow_zero=False):
    if type(seconds) is not int or not (0 if allow_zero else 1) <= seconds <= 2**31 - 1:
        raise ValueError("Duration must be a bounded integer number of seconds")
    return seconds


def validate_claim(claim, scope):
    if claim.scope != scope:
        raise ClaimRejected("Synthetic work unavailable")
    if (any(not isinstance(value, UUID) for value in (claim.job_id, claim.bound_attempt_id,
                                                     claim.execution_id, claim.owner_id))
            or type(claim.fence) is not int or claim.fence < 1
            or type(claim.state_version) is not int or claim.state_version < 1
            or not isinstance(claim.expires_at, datetime) or claim.expires_at.tzinfo is None):
        raise ValueError("Invalid synthetic claim token")


def canonical_metadata(value):
    if type(value) is not dict:
        raise ValueError("Metadata must be a JSON object")
    _json_value(value)
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text, hashlib.sha256(text.encode('utf-8')).hexdigest()


def run_synthetic_once(repository: SyntheticWork, job_id: UUID, owner_id: UUID, *, lease_seconds: int):
    """Explicit fixture call: metadata only, always retaining the review requirement."""
    claim = repository.claim(job_id, owner_id, lease_seconds)
    if claim is None:
        return None
    claim = repository.renew(claim, lease_seconds)
    claim = repository.progress(claim, {"step": "synthetic", "steps": 1})
    return repository.complete(claim, {"synthetic": True, "review_required": True, "job_id": str(job_id)})
