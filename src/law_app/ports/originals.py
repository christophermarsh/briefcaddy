"""Scoped immutable original bytes; no caller-selected paths."""
from dataclasses import dataclass
import re
from typing import Protocol
from uuid import UUID

from law_app.ports.portal_intents import FirmEnrollment, RevisionReference


class OriginalConflict(ValueError):
    """An immutable identity already contains different bytes."""


class OriginalUnavailable(LookupError):
    """Missing, unsafe, incomplete or corrupt original."""


@dataclass(frozen=True)
class OriginalRevision:
    scope: FirmEnrollment
    intake_id: UUID
    revision_id: UUID
    sha256: str
    byte_length: int
    locator: str

    def reference(self):
        return RevisionReference(self.revision_id, self.sha256, self.byte_length)


def original_key(scope, intake_id, revision_id):
    identities = (scope.firm_id, scope.enrollment_id, intake_id, revision_id)
    if any(not isinstance(value, UUID) for value in identities):
        raise ValueError("Scoped UUID identities required")
    return tuple(value.hex for value in identities)


def original_locator(scope, intake_id, revision_id):
    return 'original:v1:' + ':'.join(original_key(scope, intake_id, revision_id))


def validate_revision(revision, scope, maximum):
    if revision.scope != scope:
        raise OriginalUnavailable("Original unavailable")
    expected = original_locator(scope, revision.intake_id, revision.revision_id)
    if (revision.locator != expected or not isinstance(revision.sha256, str)
            or not re.fullmatch('[0-9a-f]{64}', revision.sha256)
            or type(revision.byte_length) is not int or not 0 <= revision.byte_length <= maximum):
        raise OriginalUnavailable("Original unavailable")


class Originals(Protocol):
    def publish(self, intake_id: UUID, revision_id: UUID, data: bytes) -> OriginalRevision: ...
    def verified_read(self, revision: OriginalRevision) -> bytes: ...
