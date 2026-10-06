"""Explicit service-message signoff and the installation dispatch/revoke gate.

This backend does not make legacy callers safe until they adopt it. Credentials
created here remain unusable until a provider result is durably accepted. The
gate precedes store/auth locks, spans main and prospect stores, and deliberately
remains held through the provider's bounded operation. No delivery claim.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading

import clock
import events
import oslock
from portal.queue_bridge import _atomic, _safe
from portal.store import CLIENT_ID, LINK_TTL, _hash, _now

CHANNELS = {"email", "sms", "whatsapp"}
HASH = re.compile(r"[0-9a-f]{64}")
FILE = "communication_consent.json"
MAX_JSON = 1024 * 1024
MAX_EVIDENCE = 4 * 1024 * 1024
_LOCAL = threading.local()


@dataclass(frozen=True)
class Scope:
    """Trusted composition supplies the installation and exact store roots."""
    root: Path
    portal: Path
    cases: Path

    def __post_init__(self):
        for name in ("root", "portal", "cases"):
            object.__setattr__(self, name, _safe(Path(getattr(self, name))))
        main = (self.data / "portal", self.data / "clients")
        prospect = (self.data / "portal" / "prospects", self.data / "prospects")
        if (self.portal, self.cases) not in (main, prospect):
            raise ValueError("Communication stores must belong to this installation.")
        # Explicit scope never inherits another installation's mutable stores.
        for key, expected in {"I485_RULES_APPROVED": self.data / "rules_approved.json",
                              "I485_MAINTENANCE_LOG": self.data / "maintenance_log.json",
                              "I485_EVENTS": self.data / "events.jsonl",
                              "I485_SETTINGS": self.data / "settings.json",
                              "I485_CASES": self.data / "clients", "I485_PROSPECTS": self.data / "prospects",
                              "PORTAL_DATA": self.data / "portal"}.items():
            if os.environ.get(key) and _safe(Path(os.environ[key])) != expected:
                raise ValueError("Communication configuration belongs to another installation.")

    @property
    def data(self):
        return self.root / "data"

    @property
    def kind(self):
        return "prospect" if self.portal.name == "prospects" else "client"

    def check(self, store, client):
        if _safe(store.root) != self.portal or not isinstance(client, str) or not CLIENT_ID.fullmatch(client):
            raise ValueError("Incorrect communication store or client.")
        return _safe(store.client_dir(client))


@contextmanager
def gate(scope, timeout=10):
    """Reentrant only within this thread; OS ownership serializes all processes."""
    path = str(_safe(scope.data / "communication.lock"))
    held = getattr(_LOCAL, "held", {})
    if held.get(path):
        held[path] += 1
        try:
            yield
        finally:
            held[path] -= 1
        return
    if held:
        raise ValueError("Nested communication gates for different installations are forbidden.")
    with oslock.locked(path, timeout=timeout):
        _LOCAL.held = {path: 1}
        try:
            yield
        finally:
            _LOCAL.held = {}


@contextmanager
def data_gate(data):
    """Shared writers use the same canonical installation gate, before RMW.

    Historical noninstallation utility/test paths cannot authenticate or send;
    they retain their existing storage behavior. Canonical data paths validate
    explicit installation overrides rather than silently switching firms.
    """
    data = _safe(Path(data))
    if data.name.casefold() != "data":
        yield
        return
    scope = Scope(data.parent, data / "portal", data / "clients")
    with gate(scope):
        yield


def data_mutation(data_source):
    from functools import wraps
    def decorate(fn):
        @wraps(fn)
        def guarded(*args, **kwargs):
            with data_gate(data_source()):
                return fn(*args, **kwargs)
        return guarded
    return decorate


def _read(path, default=None):
    path = _safe(path)
    if not path.exists():
        return default
    if not path.is_file() or path.stat().st_size > MAX_JSON:
        raise ValueError("Communication record is damaged.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Communication record is damaged.")
    return value


def staff(scope, email, *, case=None, attorney=False):
    """Reload account and ACL; role/name supplied in a request is never authority."""
    from review.auth import Accounts
    import restricted
    actor = next((u for u in Accounts(_safe(scope.data / "review_users.json")).users() if u.get("email") == email), None)
    roles = {"attorney"} if attorney else {"attorney", "paralegal"}
    if not actor or actor.get("active") is not True or actor.get("role") not in roles:
        raise PermissionError("A current authorized staff account is required.")
    if case is not None and not restricted.visible_to(actor, case):
        raise PermissionError("This case is unavailable to this account.")
    return actor


def _closed_case(scope, store, client):
    """One existing attorney-approved end snapshot, never general access."""
    import engagement
    import purge
    from client_file import allowed
    case = _safe(scope.cases / client)
    allowed(case)  # existing Q1 wait/destroyed guard
    purges = _read(scope.data / purge.PURGES_FILE, {"cases": {}}).get("cases")
    destroyed = _read(scope.data / engagement.DESTROYED_FILE, {"cases": []}).get("cases")
    if (not isinstance(purges, dict) or any(not isinstance(row, dict) for row in purges.values())
            or not isinstance(destroyed, list) or any(not isinstance(row, dict) for row in destroyed)):
        raise ValueError("Case destruction state is unavailable.")
    rec = _read(case / engagement.FILE)
    end = (rec or {}).get("end")
    if (not isinstance(end, dict) or end.get("state") not in ("closed", "withdrawn", "transferred")
            or not isinstance(end.get("on"), str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", end["on"])
            or end.get("role") != "attorney" or not isinstance(end.get("by"), str) or not end["by"].strip()
            or not isinstance(end.get("letter"), str) or not re.fullmatch(r"L[0-9]+", end["letter"])
            or not isinstance(rec.get("letters"), list) or any(not isinstance(row, dict) for row in rec["letters"])):
        raise ValueError("Current approved closing artifact is unavailable.")
    if datetime.fromisoformat(end["on"]).date() > clock.today():
        raise ValueError("Current case end is unavailable.")
    letters = [row for row in rec["letters"] if row.get("id") == end["letter"]]
    if len(letters) != 1 or letters[0].get("approved") is not True or letters[0].get("kind") != engagement.LETTER_FOR[end["state"]]:
        raise ValueError("Current approved closing artifact is unavailable.")
    letter = letters[0]
    engagement._approved_wording(letter)
    shown = engagement._portal_letter(letter)
    current = _read(scope.check(store, client) / "engagement.json", {}).get("ended")
    if (not isinstance(current, dict) or current.get("state") != end["state"]
            or current.get("since") != end.get("on") or current.get("letter") != shown):
        raise ValueError("Current closing artifact does not match this case end.")
    return end, shown


def _open(scope, store, client, *, automatic=False, closed_artifacts=False, contact_recovery=False):
    from case_assignment import Assignments
    import jobs
    import restricted
    import conflicts
    folder = scope.check(store, client)
    case = _safe(scope.cases / client)
    if not case.is_dir():
        raise ValueError("This case is unavailable.")
    if closed_artifacts:
        _closed_case(scope, store, client)
        check = _read(case / conflicts.FILE, {})
        decision = check.get("decision")
        if decision is not None and (not isinstance(decision, dict) or decision.get("decision") not in conflicts.DECISIONS):
            raise ValueError("Case conflict state is unavailable.")
        if (decision or {}).get("decision") == "declined" or check.get("abandoned"):
            raise ValueError("This client is unavailable.")
    else:
        Assignments(scope.cases, jobs.folder_for(scope.cases), lambda: [])._open(case)
    profile = store.profile(client)
    if profile.get("id") != client or store.stopped(client) or profile.get("declined_on") or profile.get("status") == "declined":
        raise ValueError("This client is unavailable.")
    if (automatic and restricted.kind_law(profile.get("track") or profile.get("docketwise_matter_type"))
            and not (case / restricted.FILE).exists() and not (case / "fact_graph.json").exists()):
        raise PermissionError("Protected case authority is unavailable.")
    if conflicts.held(case) or (automatic and not restricted.messages_allowed(case)):
        raise PermissionError("Automatic communication is held for this case.")
    if not contact_recovery:
        from .contact_transitions import current_revision
        current_revision(scope, store, client)
    return folder, profile, case


def _evidence_path(scope, reference, *, client=None):
    if (not isinstance(reference, dict) or set(reference) != {"kind", "sha256", "format"}
            or not isinstance(reference.get("sha256"), str) or not HASH.fullmatch(reference["sha256"])
            or reference.get("format") not in {"json", "pdf"}):
        raise ValueError("Invalid internal evidence reference.")
    if reference["kind"] == "consent" and isinstance(client, str) and CLIENT_ID.fullmatch(client):
        folder = scope.portal / "clients" / client / "consent-evidence"
    elif reference["kind"] == "request_wording" and isinstance(client, str) and CLIENT_ID.fullmatch(client):
        folder = scope.portal / "clients" / client / "language-evidence"
    elif reference["kind"] == "wording" and client is None:
        folder = scope.data / "wording-evidence"
    else:
        raise ValueError("Evidence does not belong to this scope.")
    return _safe(folder / (reference["sha256"] + "." + reference["format"]))


def evidence(scope, reference, *, client=None):
    path = _evidence_path(scope, reference, client=client)
    if not path.is_file() or path.stat().st_size > MAX_EVIDENCE:
        raise ValueError("Approval evidence is unavailable.")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != reference["sha256"]:
        raise ValueError("Approval evidence has changed.")
    return content


def retain_evidence(scope, content, *, actor_email, kind, format="json", store=None, client=None):
    """Immutable, bounded internal bytes; recording bytes alone grants nothing."""
    if not isinstance(content, bytes) or not content or len(content) > MAX_EVIDENCE:
        raise ValueError("Evidence is empty or too large.")
    if format == "json":
        if not isinstance(json.loads(content), dict):
            raise ValueError("Evidence must be an object.")
    elif format == "pdf":
        from portal.store import check_pdf
        check_pdf(content)
    else:
        raise ValueError("Unsupported evidence format.")
    ref = {"kind": kind, "sha256": hashlib.sha256(content).hexdigest(), "format": format}
    with gate(scope):
        if kind in ("consent", "request_wording"):
            if store is None:
                raise ValueError("Explicit client store is required.")
            _, _, case = _open(scope, store, client)
            staff(scope, actor_email, case=case)
        else:
            staff(scope, actor_email, attorney=True)
        path = _evidence_path(scope, ref, client=client)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if evidence(scope, ref, client=client) != content:
                raise ValueError("Evidence collision.") from None
        return ref


def destination(scope, client, channel, profile):
    from .contact_access import normalize_email, normalize_phone
    if channel not in CHANNELS:
        raise ValueError("Unknown communication channel.")
    value = profile.get("email" if channel == "email" else "phone") or ""
    value = normalize_email(value) if channel == "email" else normalize_phone(value)
    bound = [str(scope.portal), client, channel, value]
    return value, hashlib.sha256(json.dumps(bound, separators=(",", ":")).encode()).hexdigest()


def _record(scope, store, client):
    folder = scope.check(store, client)
    record = _read(folder / FILE)
    if record is None:
        return {"version": 1, "client": client, "store": str(scope.portal), "revision": 0, "channels": {}, "history": []}
    if (record.get("version") != 1 or record.get("client") != client or record.get("store") != str(scope.portal)
            or type(record.get("revision")) is not int or record["revision"] < 0
            or not isinstance(record.get("channels"), dict) or not isinstance(record.get("history"), list)):
        raise ValueError("Communication consent is damaged.")
    for channel, row in record["channels"].items():
        if channel not in CHANNELS or not isinstance(row, dict) or row.get("state") not in {"granted", "revoked", "pending"}:
            raise ValueError("Communication consent is damaged.")
    return record


def _save(scope, store, client, record):
    if len(json.dumps(record).encode()) > MAX_JSON:
        raise ValueError("Consent history requires maintenance before another mutation.")
    _atomic(scope.check(store, client) / FILE, record)


def _audit(scope, store, client, action, who=None):
    kind, case, home = store._ledger_where(client, "portal")
    row = events.record(kind, action, "Service communication permission updated.", case=case, home=home, who=who, version=1)
    return "recorded" if row else "unavailable"


def grant(scope, store, client, channel, *, actor_email, evidence_ref, client_approved_at,
          notice_version, language, source_kind, approval_description):
    """Staff records actual documented client approval; never an import boolean.

    Client-portal grants require the later protected principal/notice endpoint.
    A legal signature or a generic saved answer cannot call this operation.
    """
    if source_kind not in {"in_person", "client_written", "client_call"} or not isinstance(approval_description, str) or not approval_description.strip() or len(approval_description) > 2000:
        raise ValueError("Describe the actual client signoff and its source.")
    approved = datetime.fromisoformat(client_approved_at)
    if approved.tzinfo is None or approved > _now():
        raise ValueError("A real past client approval time is required.")
    with gate(scope):
        _, profile, case = _open(scope, store, client)
        actor = staff(scope, actor_email, case=case)
        evidence(scope, evidence_ref, client=client)
        from client_language_readiness import readiness
        ready = readiness(scope, language, notice_version=notice_version)
        if not ready["ready"] or profile.get("language") != language:
            raise ValueError("Current notice and language reviews are required.")
        _, bound = destination(scope, client, channel, profile)
        record = _record(scope, store, client)
        from .opt_out import pending_for
        if record.get("revocation_pending") or pending_for(scope, profile):
            raise ValueError("Complete pending permission recovery before recording new signoff.")
        from .contact_control import enrollment
        record["revision"] += 1
        row = {"state": "granted", "scope": "service_notifications", "channel": channel,
               "enrollment_sha256": enrollment(scope, store, client),
               "destination_sha256": bound, "method": "documented_client_approval",
               "client_approved_at": approved.isoformat(), "server_recorded_at": clock.stamp(),
               "actor": actor["email"], "actor_role": actor["role"], "source_kind": source_kind, "approval_description": approval_description.strip(),
               "evidence": evidence_ref, "notice_version": notice_version, "language": language,
               "wording_sha256": ready["digest"], "revision": record["revision"]}
        record["channels"][channel] = row
        record["history"].append(dict(row))
        # Grant is the sole migration from a false legacy switch. The record is
        # committed first: a crash before profile update remains safely denied.
        staff(scope, actor_email, case=case)
        _save(scope, store, client, record)
        with store._lock:
            _atomic(scope.check(store, client) / "profile.json", dict(profile, consent=dict(profile.get("consent") or {}) | {channel: True}))
        audit = _audit(scope, store, client, "communication_granted", actor_email)
        return dict(row, audit_status=audit)


def _unique(scope, client, channel, value):
    from .contact_access import contact_eligibility
    from .contact_transitions import _Paths
    result = contact_eligibility(scope, _Paths(scope.portal), client, channel)
    return result.get("eligible") is True and result.get("destination") == value


def _base_eligible(scope, store, client, channel, *, closed_artifacts=False):
    _, profile, _ = _open(scope, store, client, automatic=True, closed_artifacts=closed_artifacts)
    from .opt_out import pending_for
    if pending_for(scope, profile):
        return {"allowed": False, "reason": "stop_request_pending"}
    value, bound = destination(scope, client, channel, profile)
    record = _record(scope, store, client)
    row = record["channels"].get(channel) or {}
    if record.get("revocation_pending") or row.get("state") != "granted" or (profile.get("consent") or {}).get(channel) is not True:
        return {"allowed": False, "reason": "actual_client_signoff_required"}
    from .contact_control import enrollment
    # Every grant writer binds the actual incarnation under this same gate.
    # Older unbound rows remain historical evidence, never implicit permission.
    if row.get('enrollment_sha256') != enrollment(scope, store, client):
        return {'allowed': False, 'reason': 'enrollment_changed'}
    if row.get("scope") != "service_notifications" or row.get("channel") != channel or row.get("destination_sha256") != bound:
        return {"allowed": False, "reason": "contact_changed"}
    documented = (row.get("method") == "documented_client_approval" and row.get("source_kind") in ("in_person", "client_written", "client_call")
                  and isinstance(row.get("actor"), str) and "@" in row["actor"] and row.get("actor_role") in ("attorney", "paralegal"))
    portal = (row.get("method") == "client_portal" and row.get("source_kind") == "client_portal"
              and row.get("actor") == "client:" + client and row.get("actor_role") == "client")
    routine = (row.get("method") == "staff_recorded_approval" and row.get("source_kind") == "staff_attestation"
               and isinstance(row.get("actor"), str) and "@" in row["actor"] and row.get("actor_role") in ("attorney", "paralegal"))
    if (not (documented or portal or routine)
            or not isinstance(row.get("approval_description"), str) or not row["approval_description"].strip()
            or len(row["approval_description"]) > 2000
            or not isinstance(row.get("notice_version"), str) or not row["notice_version"].strip()
            or not isinstance(row.get("language"), str)
            or not isinstance(row.get("wording_sha256"), str) or not HASH.fullmatch(row["wording_sha256"])
            or type(row.get("revision")) is not int or not 1 <= row["revision"] <= record["revision"]
            or not isinstance(row.get("client_approved_at"), str) or not isinstance(row.get("server_recorded_at"), str)):
        raise ValueError("Actual client signoff provenance is unavailable.")
    approved, recorded = datetime.fromisoformat(row["client_approved_at"]), datetime.fromisoformat(row["server_recorded_at"])
    if approved.tzinfo is None or recorded.tzinfo is None or not approved <= recorded <= _now():
        raise ValueError("Actual client approval timestamps are unavailable.")
    retained = [r for r in record["history"] if isinstance(r, dict) and r.get("channel") == channel]
    if not retained or retained[-1] != row:
        raise ValueError("Client signoff history does not match current permission.")
    # The recording staff account is historical attribution. Current authority
    # was required at grant and remains required for every protected mutation;
    # ordinary staff turnover does not rewrite actual retained client signoff.
    retained_bytes = evidence(scope, row.get("evidence"), client=client)
    if portal:
        proof = json.loads(retained_bytes)
        expected = {"version": 1, "method": "client_portal", "client": client, "store": str(scope.portal),
                    "scope": "service_notifications", "channel": channel, "agree": True,
                    "destination_sha256": bound, "notice_version": row["notice_version"], "language": row["language"],
                    "wording_sha256": row["wording_sha256"], "server_recorded_at": row["server_recorded_at"],
                    "action_id": row.get("action_id"), "principal_sha256": row.get("principal_sha256")}
        if (not isinstance(proof, dict) or any(proof.get(k) != v for k, v in expected.items())
                or not isinstance(proof.get("typed_name"), str) or not 1 <= len(proof["typed_name"].strip()) <= 120
                or not isinstance(row.get("action_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", row["action_id"])
                or not isinstance(row.get("principal_sha256"), str) or not HASH.fullmatch(row["principal_sha256"])):
            raise ValueError("Explicit own-client signoff proof is unavailable.")
    if routine:
        from .staff_access import DIGEST, NOTICE
        proof = json.loads(retained_bytes)
        if (proof.get("version") != 1 or proof.get("method") != "staff_recorded_approval"
                or proof.get("client") != client or proof.get("store") != str(scope.portal)
                or proof.get("actor") != row["actor"] or proof.get("actor_role") != row["actor_role"]
                or proof.get("agreed") is not True or channel not in proof.get("channels", [])
                or proof.get("destinations", {}).get(channel) != bound or proof.get("recorded_at") != row["server_recorded_at"]
                or row.get("notice_version") != NOTICE):
            raise ValueError("Recorded client permission is unavailable.")
        ready = {"ready": True, "digest": DIGEST}
    else:
        from client_language_readiness import readiness
        ready = readiness(scope, profile.get("language"), notice_version=row.get("notice_version"))
    if not ready["ready"] or row.get("language") != profile.get("language") or row.get("wording_sha256") != ready.get("digest"):
        return {"allowed": False, "reason": "wording_review_required"}
    if not _unique(scope, client, channel, value):
        return {"allowed": False, "reason": "ambiguous_destination"}
    from .contact_transitions import current_revision
    from .contact_access import contact_eligibility
    contact = contact_eligibility(scope, store, client, channel)
    if not contact["eligible"]:
        return {"allowed": False, "reason": contact["reason"]}
    return {"allowed": True, "destination": value, "destination_sha256": bound,
            "contact_revision": current_revision(scope, store, client), "contact_group_sha256": contact["contact_group_sha256"],
            "revision": record["revision"], "wording_sha256": ready["digest"], "language": row["language"]}


def _eligible(scope, store, client, channel, *, closed_artifacts=False):
    current = _base_eligible(scope, store, client, channel, closed_artifacts=closed_artifacts)
    if current["allowed"] and channel != "email":
        from .contact_control import status
        if not status(scope, store, client, channel, closed_artifacts=closed_artifacts)["verified"]:
            return {"allowed": False, "reason": "contact_control_required"}
    return current


def eligibility(scope, store, client, channel):
    with gate(scope):
        try:
            return _eligible(scope, store, client, channel)
        except (ValueError, OSError, PermissionError, LookupError, TypeError, KeyError):
            return {"allowed": False, "reason": "permission_or_evidence_unavailable"}


def revoke(scope, store, client, channels, *, actor_email, requested_at=None):
    if not channels or not set(channels) <= CHANNELS:
        raise ValueError("Choose explicit channels to revoke.")
    with gate(scope):
        scope.check(store, client)
        staff(scope, actor_email, case=_safe(scope.cases / client))
        return _revoke(scope, store, client, channels, actor_email=actor_email, requested_at=requested_at)


def _revoke(scope, store, client, channels, *, actor_email=None, requested_at=None, operation_id=None, operation_source="authenticated_provider_stop"):
    """Trusted STOP/contact adapter identity; source is never a public body field."""
    if operation_source not in {"authenticated_provider_stop", "contact_transition", "promotion"}:
        raise ValueError("Unknown internal permission operation source.")
    with gate(scope):
        scope.check(store, client)
        store.profile(client)  # no resurrection after an absent/purged profile
        record = _record(scope, store, client)
        previous = next((r for r in record["history"] if operation_id and isinstance(r, dict) and r.get("operation_id") == operation_id), None)
        if previous is not None and (previous.get("channels") != sorted(set(channels)) or previous.get("source") != operation_source):
            raise ValueError("STOP operation identity was reused.")
        if previous is None:
            record["revision"] += 1
            intent = {"channels": sorted(set(channels)), "requested_at": requested_at or clock.stamp(), "recorded_at": clock.stamp(), "actor": actor_email,
                      **({"operation_id": operation_id, "source": operation_source} if operation_id else {})}
            record["revocation_pending"] = intent
            for channel in channels:
                record["channels"][channel] = {"state": "revoked", "revision": record["revision"], **intent}
            record["history"].append({"action": "revoke", "revision": record["revision"], **intent})
            _save(scope, store, client, record)  # durable denial precedes cleanup
        with store._lock:
            auth = store._auth()
            # All old credentials for this client depend on the global revision.
            for group in ("links", "sessions"):
                auth[group] = {k: v for k, v in auth[group].items() if v.get("client") != client}
            _atomic(scope.portal / "auth.json", auth)
        record.pop("revocation_pending", None)
        record["revoked_effective_at"] = clock.stamp()
        _save(scope, store, client, record)
        audit = "retained_history" if previous is not None else _audit(scope, store, client, "communication_revoked", actor_email)
        return {"state": "revoked", "effective_at": record["revoked_effective_at"], "audit_status": audit}


def dispatch(scope, store, client, channel, provider, *, credential=True):
    return _dispatch(scope, store, client, channel, provider, credential=credential,
                     purpose="sign_in" if credential else "notification")


def verification_dispatch(scope, store, client, channel, provider, *, actor_email):
    """Protected neutral verification caller only; ordinary sends cannot bypass control."""
    with gate(scope):
        if channel not in {"sms", "whatsapp"}:
            raise ValueError("Choose an explicit supported phone channel.")
        _, _, case = _open(scope, store, client)
        staff(scope, actor_email, case=case)
        return _dispatch(scope, store, client, channel, provider, credential=True, purpose="verify_contact")


def _dispatch(scope, store, client, channel, provider, *, credential, purpose):
    """provider(destination, token|None) must use a bounded network timeout.

    Only {status:'sent'} is acceptance. Outbox/unknown/failure never activates.
    The provider receives a currently unusable token; early redemption denies.
    """
    with gate(scope):
        try:
            eligible = _base_eligible(scope, store, client, channel) if purpose == "verify_contact" else eligibility(scope, store, client, channel)
        except (ValueError, OSError, PermissionError, LookupError, TypeError, KeyError):
            eligible = {"allowed": False, "reason": "permission_or_evidence_unavailable"}
        if not eligible["allowed"]:
            return {"status": "held", "reason": eligible["reason"]}
        from .contact_control import enrollment
        try:
            eligible["enrollment_sha256"] = enrollment(scope, store, client)
        except (ValueError, OSError, LookupError):
            return {"status": "held", "reason": "original_enrollment_required"}
        identity = secrets.token_hex(16)
        attempt_path = _safe(scope.check(store, client) / "communication-attempts" / (identity + ".json"))
        token = secrets.token_urlsafe(32) if credential else None
        attempt = {"version": 1, "id": identity, "client": client, "store": str(scope.portal), "channel": channel,
                   "state": "pending", "created_at": clock.stamp(), "purpose": purpose, **eligible}
        attempt.pop("destination")  # no duplicate clear contact in working proof
        attempt.pop("allowed")
        _atomic(attempt_path, attempt)
        if token:
            with store._lock:
                auth = store._auth()
                auth["links"][_hash(token)] = {"client": client, "expires": (_now() + LINK_TTL).isoformat(),
                                               "active": False, "communication": identity, "channel": channel,
                                               "revision": eligible["revision"], "destination_sha256": eligible["destination_sha256"],
                                               "contact_revision": eligible["contact_revision"], "contact_group_sha256": eligible["contact_group_sha256"],
                                               "wording_sha256": eligible["wording_sha256"], "purpose": purpose,
                                               "enrollment_sha256": eligible["enrollment_sha256"]}
                _atomic(scope.portal / "auth.json", auth)
        try:
            result = provider(eligible["destination"], token)
        except Exception:  # provider uncertainty is not acceptance; no secret exception text
            result = {"status": "unknown"}
        status = result.get("status") if isinstance(result, dict) else "unknown"
        attempt["state"] = "accepted" if status == "sent" else "unaccepted"
        attempt["provider_status"] = status if status in {"sent", "failed", "dry-run", "queued", "unknown"} else "unknown"
        attempt["recorded_at"] = clock.stamp()
        _atomic(attempt_path, attempt)
        active = False
        current = _base_eligible(scope, store, client, channel) if purpose == "verify_contact" else eligibility(scope, store, client, channel)
        if (token and attempt["state"] == "accepted" and current["allowed"]
                and enrollment(scope, store, client) == eligible["enrollment_sha256"]
                and all(current.get(k) == eligible.get(k) for k in ("revision", "destination_sha256", "wording_sha256", "contact_revision", "contact_group_sha256"))):
            with store._lock:
                auth = store._auth()
                if _hash(token) in auth["links"]:
                    auth["links"][_hash(token)]["active"] = True
                    _atomic(scope.portal / "auth.json", auth)
                    active = True
        return {"status": attempt["provider_status"], "attempt": identity, "credential_active": active}


def credential_valid(scope, store, entry, *, closed_artifacts=False):
    """Auth integration must call under the gate; legacy/unbound rows deny."""
    with gate(scope):
        try:
            if (not isinstance(entry, dict) or entry.get("active") is not True
                    or entry.get("purpose") not in {"sign_in", "verify_contact"}
                    or type(entry.get("revision")) is not int or type(entry.get("contact_revision")) is not int
                    or not isinstance(entry.get("communication"), str) or not re.fullmatch(r"[0-9a-f]{32}", entry["communication"])
                    or clock.parse(entry["expires"]) <= _now()):
                return False
            client = entry["client"]
            from .contact_control import enrollment
            if entry.get("enrollment_sha256") != enrollment(scope, store, client):
                return False
            if entry["purpose"] == "verify_contact":
                if closed_artifacts or entry["channel"] not in {"sms", "whatsapp"}:
                    return False
                current = _base_eligible(scope, store, client, entry["channel"])
            else:
                current = _eligible(scope, store, client, entry["channel"], closed_artifacts=True) if closed_artifacts else eligibility(scope, store, client, entry["channel"])
            if not current["allowed"] or any(entry.get(k) != current.get(k) for k in ("revision", "destination_sha256", "wording_sha256", "contact_revision", "contact_group_sha256")):
                return False
            attempt = _read(scope.check(store, client) / "communication-attempts" / (entry["communication"] + ".json"))
            if (not attempt or attempt.get("provider_status") != "sent"
                    or not isinstance(attempt.get("created_at"), str) or not isinstance(attempt.get("recorded_at"), str)):
                return False
            created, recorded = datetime.fromisoformat(attempt["created_at"]), datetime.fromisoformat(attempt["recorded_at"])
            if created.tzinfo is None or recorded.tzinfo is None or not created <= recorded <= _now():
                return False
            return bool(attempt and type(attempt.get("version")) is int and attempt["version"] == 1 and attempt.get("state") == "accepted"
                        and attempt.get("id") == entry["communication"] and attempt.get("client") == client
                        and attempt.get("store") == str(scope.portal) and attempt.get("channel") == entry["channel"]
                        and attempt.get("purpose") == entry["purpose"] and attempt.get("enrollment_sha256") == entry["enrollment_sha256"]
                        and all(attempt.get(k) == entry.get(k) for k in ("revision", "destination_sha256", "wording_sha256", "contact_revision", "contact_group_sha256")))
        except (ValueError, OSError, LookupError, KeyError, TypeError):
            return False


def closed_projection(scope, store, session):
    """Own prior accepted session: only the existing approved end snapshot."""
    with gate(scope):
        client = store.session_client(session, closed_artifacts=True)
        if client is None:
            raise PermissionError("Current closed-artifact access is required.")
        end, letter = _closed_case(scope, store, client)
        profile = store.profile(client)
        return {"first_name": (profile.get("name") or "").split(" ")[0], "language": profile["language"],
                "closed": {"since": end["on"], "state": end["state"], "letter": letter},
                "progress": 100, "messages": []}


def prepare_profile_change(scope, store, client):
    """Called under gate before contact/permission mutation, including imports."""
    path = scope.check(store, client) / FILE
    if not path.exists():
        return False
    record = _record(scope, store, client)
    record["revision"] += 1
    record["revocation_pending"] = {"action": "profile_changed", "recorded_at": clock.stamp()}
    record["history"].append({"action": "profile_changed", "revision": record["revision"], "at": clock.stamp()})
    _save(scope, store, client, record)
    return True


def finish_profile_change(scope, store, client, pending, *, preserve_questionnaire=False):
    # Caller holds gate -> store lock. Any failure leaves persisted denial.
    store.end_sessions(client, preserve_questionnaire=preserve_questionnaire)
    if pending:
        record = _record(scope, store, client)
        record.pop("revocation_pending", None)
        _save(scope, store, client, record)


def _manual_binding(profile):
    return hashlib.sha256(json.dumps([profile.get(k) for k in ("id", "email", "phone", "language")], separators=(",", ":")).encode()).hexdigest()


def manual_bootstrap(scope, store, client, *, actor_email):
    """Authenticated staff hands over consent-only access; no contact-control proof."""
    with gate(scope):
        _, profile, case = _open(scope, store, client)
        staff(scope, actor_email, case=case)
        from client_language_readiness import readiness
        ready = readiness(scope, profile.get("language"))
        if not ready["ready"]:
            raise ValueError("Reviewed client signoff wording is required before assisted consent access.")
        from .opt_out import pending_for
        if _record(scope, store, client).get("revocation_pending") or pending_for(scope, profile):
            raise ValueError("Pending permission recovery prevents assisted consent access.")
        token = secrets.token_urlsafe(32)
        row = {"client": client, "store": str(scope.portal), "expires": (_now() + LINK_TTL).isoformat(),
               "purpose": "consent_only", "active": True, "bootstrap_actor": actor_email,
               "profile_sha256": _manual_binding(profile), "wording_sha256": ready["digest"],
               "notice_version": ready["notice_version"], "email_control": False}
        with store._lock:
            auth = store._auth()
            auth["links"][_hash(token)] = row
            _atomic(scope.portal / "auth.json", auth)
        _audit(scope, store, client, "consent_access_shown", actor_email)
        return token


def manual_valid(scope, store, row):
    with gate(scope):
        try:
            if (not isinstance(row, dict) or row.get("purpose") != "consent_only" or row.get("active") is not True
                    or row.get("store") != str(scope.portal) or row.get("email_control") is not False
                    or clock.parse(row["expires"]) <= _now()):
                return False
            _, profile, case = _open(scope, store, row["client"])
            staff(scope, row.get("bootstrap_actor"), case=case)
            record = _record(scope, store, row["client"])
            from .opt_out import pending_for
            if record.get("revocation_pending") or pending_for(scope, profile) or row.get("profile_sha256") != _manual_binding(profile):
                return False
            from client_language_readiness import readiness
            current = readiness(scope, profile.get("language"), notice_version=row.get("notice_version"))
            return current["ready"] and row.get("wording_sha256") == current.get("digest")
        except (ValueError, PermissionError, OSError, LookupError, KeyError, TypeError):
            return False


def _client_principal(scope, store, session):
    """Own current cookie only; a request client ID or staff cookie is no proof."""
    if not isinstance(session, str) or not session:
        raise PermissionError("Current own-client consent access is required.")
    client = store.session_client(session, consent_only=True)
    if client is None:
        raise PermissionError("Current own-client consent access is required.")
    _, profile, _ = _open(scope, store, client)
    with store._lock:
        row = store._auth()["sessions"].get(_hash(session))
    if not isinstance(row, dict) or row.get("client") != client:
        raise PermissionError("Current own-client consent access is required.")
    return client, profile, row


def client_context(scope, store, session):
    """Exact current notice/contact options for this authenticated client only."""
    from client_language_readiness import readiness, _notice
    from .contact_control import status
    from .contact_access import contact_eligibility
    with gate(scope):
        client, profile, principal = _client_principal(scope, store, session)
        ready = readiness(scope, profile.get("language"))
        if not ready["ready"]:
            raise ValueError("Current reviewed signoff wording is required.")
        record = _record(scope, store, client)
        options = []
        for channel in sorted(CHANNELS):
            try:
                value, bound = destination(scope, client, channel, profile)
            except ValueError:
                continue
            options.append({"channel": channel, "destination": value, "destination_sha256": bound,
                            "state": (record["channels"].get(channel) or {}).get("state", "pending"),
                            "choice_ready": contact_eligibility(scope, store, client, channel)["eligible"],
                            "control_verified": status(scope, store, client, channel)["verified"],
                            "dispatch_ready": eligibility(scope, store, client, channel)["allowed"]})
        return {"scope": "service_notifications", "notice_version": ready["notice_version"], "language": profile["language"],
                "notice": _notice(scope.root)["texts"][profile["language"]], "wording_sha256": ready["digest"],
                "revision": record["revision"], "channels": options, "consent_only": principal.get("purpose") != "sign_in",
                "access_purpose": principal.get("purpose"),
                "verified_channel": principal["channel"] if principal.get("purpose") == "verify_contact" else None,
                "email_control": False if principal.get("purpose") == "consent_only" else status(scope, store, client, "email")["verified"]}


def client_grant(scope, store, session, body):
    """Explicit service choice; legal signatures, saved answers and flags do not call this."""
    keys = {"scope", "channel", "agree", "typed_name", "notice_version", "language", "wording_sha256",
            "destination_sha256", "revision", "action_id"}
    if (not isinstance(body, dict) or set(body) != keys or body.get("scope") != "service_notifications"
            or body.get("agree") is not True or body.get("channel") not in ("email", "sms", "whatsapp")
            or not isinstance(body.get("typed_name"), str) or not 1 <= len(body["typed_name"].strip()) <= 120
            or type(body.get("revision")) is not int or body["revision"] < 0
            or not isinstance(body.get("action_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", body["action_id"])):
        raise ValueError("Explicit channel, service scope, notice and actual client signoff are required.")
    with gate(scope):
        client, profile, principal = _client_principal(scope, store, session)
        context = client_context(scope, store, session)
        option = next((row for row in context["channels"] if row["channel"] == body["channel"]), None)
        if (option is None or any(body[key] != context[key] for key in ("scope", "notice_version", "language", "wording_sha256"))
                or body["destination_sha256"] != option["destination_sha256"]):
            raise ValueError("The current notice or destination changed; read it again before signoff.")
        record = _record(scope, store, client)
        channel = body["channel"]
        request_digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        previous = next((row for row in record["history"] if isinstance(row, dict) and row.get("method") == "client_portal"
                         and row.get("action_id") == body["action_id"]), None)
        if previous is not None:
            if (previous.get("request_sha256") != request_digest or previous.get("principal_sha256") != _hash(session)
                    or record["channels"].get(channel) != previous or record.get("revocation_pending")):
                raise ValueError("This signoff action is unavailable or was reused with different content.")
            evidence(scope, previous["evidence"], client=client)
            # Only the same still-current grant can recover a failed legacy-flag
            # write. Contact/revoke mutations prevent this branch by revision.
            if record["revision"] != previous["revision"]:
                raise ValueError("Permission changed after this signoff action.")
            _client_principal(scope, store, session)
            with store._lock:
                _atomic(scope.check(store, client) / "profile.json", dict(profile, consent=dict(profile.get("consent") or {}) | {channel: True}))
            return {"state": "granted", "channel": channel, "revision": previous["revision"], "audit_status": "retained_history", "duplicate": True}
        if body["revision"] != record["revision"] or record.get("revocation_pending"):
            raise ValueError("Permission changed; read the current notice again.")
        now = clock.stamp()
        proof = {"version": 1, "method": "client_portal", "client": client, "store": str(scope.portal),
                 **body, "principal_sha256": _hash(session), "server_recorded_at": now,
                 "bootstrap_actor": principal.get("bootstrap_actor"), "email_control": context["email_control"]}
        content = json.dumps(proof, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ref = {"kind": "consent", "sha256": hashlib.sha256(content).hexdigest(), "format": "json"}
        path = _evidence_path(scope, ref, client=client)
        _client_principal(scope, store, session)  # fresh immediately before retention
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if evidence(scope, ref, client=client) != content:
                raise ValueError("Client evidence collision.") from None
        _client_principal(scope, store, session)  # fresh before grant publication
        record["revision"] += 1
        from .contact_control import enrollment
        row = {"state": "granted", "scope": "service_notifications", "channel": channel,
               "enrollment_sha256": enrollment(scope, store, client),
               "destination_sha256": option["destination_sha256"], "method": "client_portal", "source_kind": "client_portal",
               "client_approved_at": now, "server_recorded_at": now, "actor": "client:" + client, "actor_role": "client",
               "approval_description": "Explicit own-client portal service-message signoff.", "evidence": ref,
               "notice_version": context["notice_version"], "language": context["language"], "wording_sha256": context["wording_sha256"],
               "revision": record["revision"], "action_id": body["action_id"], "principal_sha256": _hash(session), "request_sha256": request_digest}
        record["channels"][channel] = row
        record["history"].append(dict(row))
        _save(scope, store, client, record)  # durable proof precedes flag migration
        with store._lock:
            _atomic(scope.check(store, client) / "profile.json", dict(profile, consent=dict(profile.get("consent") or {}) | {channel: True}))
        return {"state": "granted", "channel": channel, "revision": row["revision"], "audit_status": _audit(scope, store, client, "communication_granted", "The client")}


def client_revoke(scope, store, session, body):
    if (not isinstance(body, dict) or set(body) != {"scope", "channels"} or body.get("scope") != "service_notifications"
            or not isinstance(body.get("channels"), list) or not body["channels"] or len(body["channels"]) > 3
            or any(channel not in ("email", "sms", "whatsapp") for channel in body["channels"])):
        raise ValueError("Choose explicit service-message channels to revoke.")
    with gate(scope):
        client, _, _ = _client_principal(scope, store, session)
        return _revoke(scope, store, client, body["channels"], actor_email="client:" + client)
