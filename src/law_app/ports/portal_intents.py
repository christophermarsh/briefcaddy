"""Version-one portal intent input; scopes come from trusted composition."""
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class FirmEnrollment:
    firm_id: UUID
    enrollment_id: UUID


@dataclass(frozen=True)
class RevisionReference:
    revision_id: UUID
    sha256: str
    byte_length: int


@dataclass(frozen=True)
class PortalIntentRequest:
    intake_id: UUID
    generation_id: str
    operation_id: str
    authority_evidence_id: UUID
    revisions: tuple[RevisionReference, ...]
    processing_version: str
    options: dict[str, Any]


@dataclass(frozen=True)
class PortalIntentReceipt:
    job_id: UUID
    attempt_id: UUID
    outbox_id: UUID
    reused: bool


class IntentConflict(ValueError):
    """The retained operation has different canonical inputs."""


class IntentUnavailable(LookupError):
    """Scope/input unavailable; do not disclose which reference was missing."""


class IntentPersistenceFailure(RuntimeError):
    """The transaction could not commit; reconcile an uncertain commit before retry."""


class PortalIntents(Protocol):
    def commit_portal_intent(self, request: PortalIntentRequest) -> PortalIntentReceipt: ...
    def reconcile_portal_intent(self, request: PortalIntentRequest) -> PortalIntentReceipt: ...


def _json_value(value):
    if type(value) in (str, bool, int, float) or value is None:
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValueError("Options must contain only JSON values with string keys")


def canonical_request(request: PortalIntentRequest) -> tuple[str, str, str]:
    """Snapshot validated inputs into JSON, request SHA256 and revision manifest SHA256.

    Digest v1 preserves revision/array order and Unicode codepoints, rejects
    nonfinite numbers and uses sorted keys/compact UTF-8 JSON. No legacy rehash.
    """
    if not isinstance(request.intake_id, UUID) or not isinstance(request.authority_evidence_id, UUID):
        raise ValueError("Intake and authority evidence must have stable UUIDs")
    if any(not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32,64}", token)
           for token in (request.generation_id, request.operation_id)):
        raise ValueError("Invalid generation or operation identity")
    if not isinstance(request.processing_version, str) or not 1 <= len(request.processing_version) <= 128:
        raise ValueError("A bounded processing version is required")
    if type(request.options) is not dict:
        raise ValueError("Processing options must be a JSON object")
    _json_value(request.options)
    if not request.revisions:
        raise ValueError("Portal processing requires an immutable input revision")
    refs = []
    for ref in request.revisions:
        if (not isinstance(ref.revision_id, UUID) or not isinstance(ref.sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", ref.sha256)
                or type(ref.byte_length) is not int or not 0 <= ref.byte_length <= 2**63 - 1):
            raise ValueError("Invalid exact revision reference")
        refs.append({"revision_id": str(ref.revision_id), "sha256": ref.sha256, "byte_length": ref.byte_length})
    if len({ref["revision_id"] for ref in refs}) != len(refs):
        raise ValueError("Duplicate revision identity")
    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    manifest = encode(refs)
    payload = encode({"digest_version": 1, "kind": "portal_process", "intake_id": str(request.intake_id),
                      "generation_id": request.generation_id, "operation_id": request.operation_id,
                      "authority_evidence_id": str(request.authority_evidence_id), "revisions": refs,
                      "processing_version": request.processing_version, "options": request.options})
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest(), hashlib.sha256(manifest.encode("utf-8")).hexdigest()
