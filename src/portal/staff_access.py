"""Audited staff handover and service permission, distinct from provider proof."""
import hashlib
import json
import secrets

import clock
from . import communication_consent as consent
from .store import LINK_TTL, _hash, _now

NOTICE = "staff-service-permission-v1"
PERMISSION = "The client agreed to service messages through the selected channels."
DIGEST = hashlib.sha256(PERMISSION.encode()).hexdigest()


def _questionnaire_binding(profile):
    # Reading language is a preference, not a different questionnaire recipient.
    return consent._manual_binding(dict(profile, language=None))


def _recipient_matches(row, profile):
    if row.get("questionnaire_binding_version") == 2:
        return row.get("profile_sha256") == _questionnaire_binding(profile)
    if "questionnaire_binding_version" in row:
        return False
    # Previously issued handovers included language. Keep their recipient checks
    # while allowing the client to choose any supported reading language.
    from .bank import languages
    return any(row.get("profile_sha256") == consent._manual_binding(dict(profile, language=lang)) for lang in languages())


def permission_view(scope, store, client, *, actor_email):
    """Saved choices are recipient-bound preferences, separate from delivery eligibility."""
    with consent.gate(scope):
        _, profile, case = consent._open(scope, store, client)
        consent.staff(scope, actor_email, case=case)
        record = consent._record(scope, store, client)
        selected, provenance = [], {}
        for channel, row in record["channels"].items():
            provenance[channel] = {key: row.get(key) for key in (
                "state", "actor", "actor_role", "server_recorded_at", "recorded_at", "revision", "destination_sha256", "evidence")}
            try:
                bound = consent.destination(scope, client, channel, profile)[1]
            except ValueError:
                continue
            if row.get("state") == "granted" and row.get("destination_sha256") == bound and not record.get("revocation_pending"):
                selected.append(channel)
        snapshot = record.get("staff_preferences") or {}
        return {"revision": record["revision"], "channels": sorted(selected), "note": snapshot.get("note", ""),
                "provenance": provenance, "recorded_at": snapshot.get("recorded_at"), "actor": snapshot.get("actor")}


def record_permission(scope, store, client, *, actor_email, channels, agreed, note="", expected_revision=0):
    if agreed is not True:
        raise ValueError("Confirm the client agreed to messages through the selected channels.")
    if not isinstance(channels, list) or len(channels) > 3 or any(not isinstance(c, str) or c not in consent.CHANNELS for c in channels) or len(set(channels)) != len(channels):
        raise ValueError("Choose the channels the client agreed to.")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Read the current communication choices before saving.")
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError("Keep the permission note under 2,000 characters.")
    with consent.gate(scope):
        _, profile, case = consent._open(scope, store, client)
        actor = consent.staff(scope, actor_email, case=case)
        record = consent._record(scope, store, client)
        if record["revision"] != expected_revision:
            raise ValueError("Communication choices changed. Reopen current choices and review your unsaved draft before saving again.")
        from .opt_out import pending_for
        if record.get("revocation_pending") or pending_for(scope, profile):
            raise ValueError("Complete pending permission recovery first.")
        bindings = {channel: consent.destination(scope, client, channel, profile)[1] for channel in set(channels)}
        now = clock.stamp()
        proof = {"version": 1, "method": "staff_recorded_approval", "client": client, "store": str(scope.portal),
                 "actor": actor["email"], "actor_role": actor["role"], "channels": sorted(bindings),
                 "destinations": bindings, "agreed": True, "recorded_at": now, "note": note.strip()}
        ref = consent.retain_evidence(scope, json.dumps(proof).encode(), actor_email=actor_email,
                                      kind="consent", store=store, client=client)
        record["revision"] += 1
        # Unselected grants are explicit revocations in this complete preference
        # snapshot. They do not create invitations or revoke questionnaire access.
        for channel, previous in list(record["channels"].items()):
            if channel not in bindings and previous.get("state") == "granted":
                row = {"state": "revoked", "scope": "service_notifications", "channel": channel,
                       "destination_sha256": previous.get("destination_sha256"), "actor": actor["email"],
                       "actor_role": actor["role"], "recorded_at": now, "server_recorded_at": now,
                       "revision": record["revision"], "evidence": ref, "method": "staff_recorded_revocation"}
                record["channels"][channel] = row
                record["history"].append(dict(row, action="communication_preference_revoked"))
        from .contact_control import enrollment
        enrollment_sha = enrollment(scope, store, client)
        for channel, bound in bindings.items():
            row = {"state": "granted", "scope": "service_notifications", "channel": channel,
                   "enrollment_sha256": enrollment_sha,
                   "destination_sha256": bound, "method": "staff_recorded_approval", "source_kind": "staff_attestation",
                   "client_approved_at": now, "server_recorded_at": now, "actor": actor["email"], "actor_role": actor["role"],
                   "approval_description": PERMISSION, "evidence": ref, "notice_version": NOTICE,
                   "language": profile["language"], "wording_sha256": DIGEST, "revision": record["revision"]}
            record["channels"][channel] = row
            record["history"].append(dict(row))
        record["staff_preferences"] = {"channels": sorted(bindings), "note": note.strip(), "recorded_at": now,
                                       "actor": actor["email"], "revision": record["revision"], "evidence": ref}
        record["history"].append({"action": "staff_preferences_saved", **record["staff_preferences"]})
        consent._save(scope, store, client, record)
        with store._lock:
            consent._atomic(scope.check(store, client) / "profile.json",
                            dict(profile, consent=dict(profile.get("consent") or {}) | {channel: channel in bindings for channel in consent.CHANNELS}))
        return {"saved": True, "channels": sorted(bindings), "revision": record["revision"],
                "preferences": permission_view(scope, store, client, actor_email=actor_email),
                "audit_status": consent._audit(scope, store, client, "communication_granted", actor_email)}


def issue(scope, store, client, *, actor_email, agreed, note=""):
    if agreed is not True:
        raise ValueError("Confirm the client requested questionnaire access and you checked the recipient.")
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError("Keep the access note under 2,000 characters.")
    with consent.gate(scope):
        _, profile, case = consent._open(scope, store, client)
        actor = consent.staff(scope, actor_email, case=case)
        record = consent._record(scope, store, client)
        from .opt_out import pending_for
        if record.get("revocation_pending") or pending_for(scope, profile):
            raise ValueError("Complete pending permission recovery first.")
        proof = {"version": 1, "method": "staff_questionnaire_handover", "client": client, "store": str(scope.portal),
                 "actor": actor["email"], "actor_role": actor["role"], "agreed": True,
                 "recorded_at": clock.stamp(), "note": note.strip(), "id": secrets.token_hex(16)}
        ref = consent.retain_evidence(scope, json.dumps(proof).encode(), actor_email=actor_email,
                                      kind="consent", store=store, client=client)
        record["questionnaire_access"] = ref
        record["history"].append({"action": "questionnaire_access", "evidence": ref, "actor": actor["email"], "at": proof["recorded_at"]})
        consent._save(scope, store, client, record)
        from .contact_transitions import current_revision
        token = secrets.token_urlsafe(32)
        row = {"client": client, "store": str(scope.portal), "purpose": "staff_questionnaire", "active": True,
               "expires": (_now() + LINK_TTL).isoformat(), "actor": actor["email"], "evidence": ref,
               "revision": record["revision"], "contact_revision": current_revision(scope, store, client),
               "profile_sha256": _questionnaire_binding(profile), "questionnaire_binding_version": 2}
        with store._lock:
            auth = store._auth()
            auth["links"][_hash(token)] = row
            consent._atomic(scope.portal / "auth.json", auth)
        audit = consent._audit(scope, store, client, "questionnaire_access_shown", actor_email)
        return {"token": token, "hours": int(LINK_TTL.total_seconds() // 3600), "audit_status": audit}


def valid(scope, store, row):
    with consent.gate(scope):
        try:
            if not isinstance(row, dict) or row.get("purpose") != "staff_questionnaire" or row.get("active") is not True or clock.parse(row["expires"]) <= _now():
                return False
            client = row["client"]
            _, profile, case = consent._open(scope, store, client)
            consent.staff(scope, row.get("actor"), case=case)
            record = consent._record(scope, store, client)
            from .contact_transitions import current_revision
            from .opt_out import pending_for
            if (row.get("store") != str(scope.portal) or record.get("revocation_pending") or pending_for(scope, profile)
                    or type(row.get("revision")) is not int or not 0 <= row["revision"] <= record["revision"]
                    or any(event.get("action") == "revoke" and event.get("revision", 0) > row["revision"] for event in record["history"] if isinstance(event, dict))
                    or row.get("contact_revision") != current_revision(scope, store, client)
                    or not _recipient_matches(row, profile)
                    or row.get("evidence") != record.get("questionnaire_access")):
                return False
            proof = json.loads(consent.evidence(scope, row["evidence"], client=client))
            return (proof.get("version") == 1 and proof.get("method") == "staff_questionnaire_handover"
                    and proof.get("client") == client and proof.get("store") == str(scope.portal)
                    and proof.get("actor") == row["actor"] and proof.get("actor_role") in {"attorney", "paralegal"}
                    and proof.get("agreed") is True)
        except (ValueError, OSError, PermissionError, LookupError, KeyError, TypeError):
            return False
