"""Contact publication and same-identity enrollment recovery.

No identity attestation or contact-control verification is supplied here.
Case-owned records use the existing communication gate and consent revokes.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets

import clock
from . import contact_access as contacts
from .queue_bridge import _atomic, _safe

FILE = "portal_access.json"
MAX_BYTES = 1024 * 1024
ID = re.compile(r"[0-9a-f]{32}")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize_profile(profile):
    profile = dict(profile)
    for key in ("email", "phone"):
        value = profile.get(key, "")
        if not isinstance(value, str):
            raise ValueError("Contact fields require text.")
        value = value.strip()
        if value:
            if key == "email":
                value = contacts.normalize_email(value)
            else:
                try:
                    value = contacts.normalize_phone(value)
                except ValueError:
                    if len(value) > 64:
                        raise ValueError("Unsupported phone context is too long.") from None
        profile[key] = value
    return profile


def initial_fields(value):
    """Bounded trusted caller setup, never arbitrary profile/body merging."""
    from .bank import FILING_BANKS
    if not isinstance(value, dict) or not set(value) <= {"filing", "office", "track", "added_by", "added_at", "prospect"}:
        raise ValueError("Unknown initial profile setup field.")
    if "filing" in value and value["filing"] not in {"i485", *FILING_BANKS}:
        raise ValueError("Unknown initial questionnaire.")
    for name in ("office", "track", "added_by"):
        if name in value and (not isinstance(value[name], str) or not value[name].strip() or len(value[name]) > 200):
            raise ValueError("Initial setup requires bounded text.")
    if "added_at" in value and (not isinstance(value["added_at"], str) or clock.parse(value["added_at"]) is None):
        raise ValueError("Initial setup time is damaged.")
    if "prospect" in value and (value["prospect"] is not True or value.get("filing") != "first_contact"):
        raise ValueError("Prospect setup requires its own first-contact questionnaire.")
    return dict(value)


def _contact_fields(profile):
    profile = normalize_profile(profile)
    phone = profile["phone"]
    try:
        phone = contacts.normalize_phone(phone) if phone else None
    except ValueError:
        phone = None
    return {"email": profile["email"] or None, "phone": phone,
            "unsupported_phone": bool(profile["phone"] and phone is None)}


class _Paths:
    def __init__(self, root):
        self.root = root

    def client_dir(self, client):
        return self.root / "clients" / client


def _composition(scope, kind, *, readonly=False):
    from .communication_consent import Scope
    from .store import PortalStore
    import prospects
    if kind == "client":
        root = scope.data / "portal"
        return Scope(scope.root, root, scope.data / "clients"), _Paths(root) if readonly else PortalStore(root)
    if kind == "prospect":
        root = scope.data / "portal/prospects"
        return Scope(scope.root, root, scope.data / "prospects"), _Paths(root) if readonly else prospects.store(scope.data / "portal")
    raise ValueError("Contact transition store is damaged.")


def _record(scope, store, client):
    from .communication_consent import _read
    path = _safe(scope.check(store, client) / FILE)
    record = _read(path)
    if record is None:
        return {"version": 1, "store": str(scope.portal), "client": client,
                "revision": 0, "state": "ready", "history": []}
    if (path.stat().st_size > MAX_BYTES or record.get("version") != 1
            or record.get("store") != str(scope.portal) or record.get("client") != client
            or type(record.get("revision")) is not int or record["revision"] < 0
            or record.get("state") not in ("ready", "pending")
            or not isinstance(record.get("history"), list)
            or any(not isinstance(row, dict) for row in record["history"])
            or not isinstance(record.get("control_receipts", []), list)
            or any(not isinstance(row, dict) for row in record.get("control_receipts", []))):
        raise ValueError("Contact access record is damaged.")
    if record["revision"] and (not record["history"] or record["history"][-1].get("revision") != record["revision"]):
        raise ValueError("Contact access history is damaged.")
    pending = record.get("pending")
    if pending is not None and (not isinstance(pending, dict) or not ID.fullmatch(pending.get("id", ""))
            or pending.get("owner_kind") not in ("client", "prospect")
            or not isinstance(pending.get("owner_client"), str)):
        raise ValueError("Contact transition ownership is damaged.")
    if record["state"] == "pending" and pending is None:
        raise ValueError("Pending contact transition is damaged.")
    return record


def _save(scope, store, client, record):
    if len(json.dumps(record).encode()) > MAX_BYTES:
        raise ValueError("Contact history requires maintenance before another transition; nothing was discarded.")
    _atomic(scope.check(store, client) / FILE, record)


def _plan(record):
    plan = record.get("transition")
    if (not isinstance(plan, dict) or not ID.fullmatch(plan.get("id", ""))
            or plan.get("state") not in ("pending", "completed")
            or not all(isinstance(plan.get(k), str) and re.fullmatch(r"[0-9a-f]{64}", plan[k]) for k in ("before", "after"))
            or not isinstance(plan.get("affected"), list) or not 1 <= len(plan["affected"]) <= 10000):
        raise ValueError("Contact transition plan is damaged.")
    proposed = plan.get("proposed")
    if (not isinstance(proposed, dict) or set(proposed) != {"email", "phone"}
            or normalize_profile(proposed) != proposed or _hash(_contact_fields(proposed)) != plan["after"]):
        raise ValueError("Contact transition proposal is damaged.")
    if "enrollment" in plan:
        snapshot = plan.get("profile_snapshot")
        if (plan["enrollment"] is not True or not isinstance(snapshot, dict)
                or not {"id", "name", "email", "phone", "language", "consent", "status", "created_at"} <= set(snapshot)
                or snapshot.get("id") != record["client"] or not isinstance(snapshot.get("name"), str)
                or not 1 <= len(snapshot["name"].strip()) <= 200 or snapshot.get("status") != "invited"
                or snapshot.get("language") not in {"en", "pt", "es", "ht"}
                or not isinstance(snapshot.get("consent"), dict) or not set(snapshot["consent"]) <= contacts.CHANNELS
                or any(type(value) is not bool for value in snapshot["consent"].values())
                or not isinstance(snapshot.get("created_at"), str) or clock.parse(snapshot["created_at"]) is None
                or normalize_profile(snapshot) != snapshot or _hash(snapshot) != plan.get("profile_sha256")
                or {key: snapshot[key] for key in ("email", "phone")} != proposed):
            raise ValueError("Enrollment snapshot is damaged.")
        initial_fields({key: value for key, value in snapshot.items() if key not in {"id", "name", "email", "phone", "language", "consent", "status", "created_at"}})
    seen = set()
    for row in plan["affected"]:
        key = (row.get("kind"), row.get("client")) if isinstance(row, dict) else None
        if (key is None or key in seen or key[0] not in ("client", "prospect")
                or not isinstance(key[1], str) or not contacts.CLIENT_ID.fullmatch(key[1])
                or not isinstance(row.get("channels"), list)
                or not row["channels"] or any(not isinstance(ch, str) or ch not in contacts.CHANNELS for ch in row["channels"])
                or not isinstance(row.get("revoke"), list) or any(not isinstance(ch, str) or ch not in row["channels"] for ch in row["revoke"])
                or type(row.get("revision")) is not int or row["revision"] < 1):
            raise ValueError("Contact transition membership is damaged.")
        seen.add(key)
    return plan


def _lifecycle(scope, client, *profiles):
    """Reuse current case policy; do not create a folder or reopen a case."""
    from case_assignment import Assignments
    import jobs
    case = _safe(scope.cases / client)
    if not case.is_dir():
        raise ValueError("A canonical protected case or prospect folder is required before contact publication.")
    for profile in profiles:
        if (profile.get("closed_on") or profile.get("declined_on") or profile.get("destroyed")
                or profile.get("status") in {"closed", "declined", "destroyed", "purged"}):
            raise ValueError("Closed or declined profiles cannot change contacts.")
    Assignments(scope.cases, jobs.folder_for(scope.cases), lambda: [])._open(case)
    if scope.kind == "prospect":
        from .communication_consent import _read
        import prospects
        prospect = _read(case / prospects.FILE, {})
        if prospect.get("declined") or prospect.get("became_client"):
            raise ValueError("Closed or promoted prospects cannot change contacts.")
    return case


def require_complete_enrollments(scope):
    """Before allocating another slug, refuse any interrupted enrollment."""
    from .promotion import allocation_guard
    allocation_guard(scope)
    rows = contacts.inventory(scope)
    for identity in rows:
        ms, st = _composition(scope, identity.store_kind, readonly=True)
        record = _record(ms, st, identity.client)
        if record.get("transition") and _plan(record).get("enrollment"):
            raise ValueError("Recover the recorded same client ID before adding another profile.")
    return rows


def prepare_enrollment(scope, store, client, proposed, *, _promotion=None):
    """Trusted add: no supplied display/import attribution becomes authority."""
    from .communication_consent import _record as permission, gate
    with gate(scope):
        own = scope.check(store, client)
        if own.exists():
            raise ValueError("An interrupted or existing identity requires explicit same-ID recovery.")
        _lifecycle(scope, client, proposed)
        if _promotion is None:
            rows = require_complete_enrollments(scope)
            enrollment_id = secrets.token_hex(16)
        else:
            from .promotion import reserved_enrollment
            enrollment_id = reserved_enrollment(scope, store, client, proposed, _promotion)
            rows = contacts.inventory(scope)  # Still strict BEFORE own first write.
        if sum(row.store_kind == scope.kind for row in rows) >= contacts.MAX_CONTACTS_PER_STORE:
            raise contacts.InventoryError("contact_inventory_limit")
        normalized = normalize_profile(proposed)
        after = _contact_fields(normalized)
        affected = {(scope.kind, client): set(contacts.CHANNELS)}
        for row in rows:
            for channel in contacts.CHANNELS:
                field = "email" if channel == "email" else "phone"
                if ((after[field] is not None and getattr(row, field) == after[field])
                        or (field == "phone" and after["unsupported_phone"] and (row.phone or row.unsupported_phone))):
                    affected.setdefault((row.store_kind, row.client), set()).add(channel)
        members = []
        for (kind, cid), channels in sorted(affected.items()):
            ms, st = _composition(scope, kind, readonly=True)
            rec = _record(ms, st, cid)
            if rec.get("pending") or rec.get("transition"):
                raise ValueError("Another contact transition requires recovery.")
            grants = permission(ms, st, cid)
            revoked = [ch for ch in sorted(channels) if (grants["channels"].get(ch) or {}).get("state") in {"granted", "pending"}]
            members.append({"kind": kind, "client": cid, "channels": sorted(channels), "revoke": revoked, "revision": rec["revision"] + 1})
        plan = {"id": enrollment_id, "state": "pending", "before": _hash(_contact_fields({})), "after": _hash(after),
                "proposed": {key: normalized[key] for key in ("email", "phone")}, "affected": members,
                "enrollment": True, "profile_snapshot": normalized, "profile_sha256": _hash(normalized)}
        record = _record(scope, store, client)
        own_member = next(row for row in members if (row["kind"], row["client"]) == (scope.kind, client))
        record.update(revision=own_member["revision"], state="pending", transition=plan,
                      pending={"id": plan["id"], "owner_kind": scope.kind, "owner_client": client})
        record["history"].append({"id": plan["id"], "revision": record["revision"], "action": "enrollment_denied", "at": clock.stamp()})
        _plan(record)  # validate complete snapshot before directory/publication effects
        _save(scope, store, client, record)
        _denials(scope, client, plan)
        return plan["id"], normalized


def current_revision(scope, store, client):
    import read_scope
    return read_scope.contact_once(("contact-revision", str(scope.root), str(store.root), client), lambda: _current_revision(scope, store, client))


def _current_revision(scope, store, client):
    """Pure current fence, including partial multi-member finalization."""
    from .promotion import assert_ready
    assert_ready(scope, store, client)
    record = _record(scope, store, client)
    # A crash can occur after the complete coordinator is durable but before
    # another member's own fence exists. The complete bounded inventory makes
    # that coordinator authoritative from its first write, without guessing.
    for identity in contacts.inventory(scope):
        ms, st = _composition(scope, identity.store_kind, readonly=True)
        owner = _record(ms, st, identity.client)
        if owner.get("transition"):
            plan = _plan(owner)
            if plan["state"] == "pending" and any((row["kind"], row["client"]) == (scope.kind, client) for row in plan["affected"]):
                raise ValueError("Complete pending contact recovery before access.")
    pending = record.get("pending")
    if pending:
        owner_scope, owner_store = _composition(scope, pending["owner_kind"], readonly=True)
        owner = _record(owner_scope, owner_store, pending["owner_client"])
        plan = _plan(owner)
        member = next((row for row in plan["affected"] if (row["kind"], row["client"]) == (scope.kind, client)), None)
        if (plan["id"] != pending["id"] or member is None or member["revision"] != record["revision"]
                or record["state"] != "ready" or plan["state"] != "completed" or owner["state"] != "ready"):
            raise ValueError("Complete pending contact recovery before access.")
    elif record["state"] != "ready":
        raise ValueError("Complete pending contact recovery before access.")
    if record.get("transition") and _plan(record)["state"] != "completed":
        raise ValueError("Complete pending contact recovery before access.")
    return record["revision"]


def _deny(scope, store, client, record, plan, member, owner_kind, owner_client):
    pending = {"id": plan["id"], "owner_kind": owner_kind, "owner_client": owner_client}
    if record.get("pending") == pending and record["revision"] == member["revision"]:
        return record
    if record.get("pending") or record["revision"] != member["revision"] - 1:
        raise ValueError("Another contact transition requires recovery.")
    record.update(revision=member["revision"], state="pending", pending=pending)
    record["history"].append({"id": plan["id"], "revision": record["revision"], "action": "contact_denied",
                              "channels": member["channels"], "at": clock.stamp()})
    _save(scope, store, client, record)
    return record


def _denials(scope, owner_client, plan):
    from .communication_consent import _revoke
    # Every member fence is durable before any grant/profile effects.
    for member in plan["affected"]:
        ms, st = _composition(scope, member["kind"])
        _deny(ms, st, member["client"], _record(ms, st, member["client"]), plan, member, scope.kind, owner_client)
    for member in plan["affected"]:
        if member["revoke"]:
            ms, st = _composition(scope, member["kind"])
            operation = _hash(["contact", plan["id"], member["kind"], member["client"]])
            _revoke(ms, st, member["client"], member["revoke"], operation_id=operation,
                    operation_source="contact_transition")


def prepare_transition(scope, store, client, old, proposed):
    """Trusted existing-profile composition; no body-selected roots/authority."""
    from .communication_consent import _record as permission, gate
    with gate(scope):
        scope.check(store, client)
        if not old:
            raise ValueError("New contact enrollment requires its separate recovery contract.")
        current = _record(scope, store, client)
        if current.get("pending") or current.get("transition"):
            raise ValueError("Explicit current-authorized contact recovery is required.")
        for identity in contacts.inventory(scope):
            ms, st = _composition(scope, identity.store_kind, readonly=True)
            coordinator = _record(ms, st, identity.client)
            if coordinator.get("transition") and any((row["kind"], row["client"]) == (scope.kind, client) for row in _plan(coordinator)["affected"]):
                raise ValueError("Complete existing contact coordinator cleanup before another transition.")
        before, after = _contact_fields(old), _contact_fields(proposed)
        if before == after:
            return None
        _lifecycle(scope, client, old, proposed)
        rows = contacts.inventory(scope)
        selected = next((row for row in rows if (row.store_kind, row.client) == (scope.kind, client)), None)
        if selected is None:
            raise ValueError("Existing contact profile is unavailable.")
        affected = {(scope.kind, client): set()}
        for channel in sorted(contacts.CHANNELS):
            field = "email" if channel == "email" else "phone"
            if before[field] == after[field] and not (field == "phone" and before["unsupported_phone"] != after["unsupported_phone"]):
                continue
            affected[(scope.kind, client)].add(channel)
            unknown_changed = field == "phone" and before["unsupported_phone"] != after["unsupported_phone"]
            for row in rows:
                value = getattr(row, field)
                if (value is not None and value in (before[field], after[field])) or (unknown_changed and (row.phone or row.unsupported_phone)):
                    affected.setdefault((row.store_kind, row.client), set()).add(channel)
        members = []
        for (kind, cid), channels in sorted(affected.items()):
            if not channels:
                continue
            ms, st = _composition(scope, kind)
            rec = _record(ms, st, cid)
            if rec.get("pending") or rec.get("transition"):
                raise ValueError("Another contact transition requires recovery.")
            grants = permission(ms, st, cid)
            revoke = [ch for ch in sorted(channels) if (grants["channels"].get(ch) or {}).get("state") in ("granted", "pending")]
            members.append({"kind": kind, "client": cid, "channels": sorted(channels), "revoke": revoke, "revision": rec["revision"] + 1})
        normalized = normalize_profile(proposed)
        plan = {"id": secrets.token_hex(16), "state": "pending", "before": _hash(before), "after": _hash(after),
                "proposed": {key: normalized[key] for key in ("email", "phone")}, "affected": members}
        own = next(row for row in members if (row["kind"], row["client"]) == (scope.kind, client))
        current["transition"] = plan
        # The complete coordinator precedes every affected-member effect.
        current.update(revision=own["revision"], state="pending",
                       pending={"id": plan["id"], "owner_kind": scope.kind, "owner_client": client})
        current["history"].append({"id": plan["id"], "revision": current["revision"], "action": "contact_denied", "channels": own["channels"], "at": clock.stamp()})
        _save(scope, store, client, current)
        _denials(scope, client, plan)
        return plan["id"]


def finish_transition(scope, store, client, identity):
    from .communication_consent import gate
    with gate(scope):
        record = _record(scope, store, client)
        plan = _plan(record)
        if plan["id"] != identity or _hash(_contact_fields(store.profile(client))) != plan["after"]:
            raise ValueError("Contact publication does not match its transition.")
        if plan.get("enrollment") and _hash(store.profile(client)) != plan["profile_sha256"]:
            raise ValueError("Published enrollment no longer matches its immutable snapshot.")
        contacts.inventory(scope)  # complete current inventory, never a stale group snapshot
        if plan["state"] == "pending":
            _denials(scope, client, plan)
            # Cleanup completes for every member before any member is ready.
            owner_ready = None
            for member in plan["affected"]:
                ms, st = _composition(scope, member["kind"])
                st.end_sessions(member["client"])
            for member in plan["affected"]:
                ms, st = _composition(scope, member["kind"])
                rec = _record(ms, st, member["client"])
                rec["state"] = "ready"
                if not any(row.get("id") == identity and row.get("action") == "contact_ready" for row in rec["history"]):
                    rec["history"].append({"id": identity, "revision": rec["revision"], "action": "contact_ready", "at": clock.stamp()})
                if (member["kind"], member["client"]) == (scope.kind, client):
                    rec["transition"] = dict(plan, state="completed")
                    # Publish coordinator ready only after all other members.
                    owner_ready = rec
                    continue
                _save(ms, st, member["client"], rec)
            if owner_ready is None:
                raise ValueError("Contact coordinator membership is damaged.")
            _save(scope, store, client, owner_ready)
        # Completed coordinator is the durable multi-write fence. Remove
        # cross-case references only afterward; ready history is own-only.
        for member in plan["affected"]:
            ms, st = _composition(scope, member["kind"])
            rec = _record(ms, st, member["client"])
            expected = {"id": identity, "owner_kind": scope.kind, "owner_client": client}
            if rec["revision"] != member["revision"] or rec["state"] != "ready" or (rec.get("pending") is not None and rec["pending"] != expected):
                raise ValueError("Contact cleanup does not own the current member revision.")
            rec.pop("pending", None)
            if (member["kind"], member["client"]) != (scope.kind, client):
                _save(ms, st, member["client"], rec)
        record = _record(scope, store, client)
        record.pop("pending", None)
        record.pop("transition", None)
        _save(scope, store, client, record)
        return {"state": "ready", "revision": record["revision"]}


def recovery_view(scope, store, client, *, actor_email):
    """Own current-ACL recovery view; no other affected identity is returned."""
    from .communication_consent import gate, staff, _open
    with gate(scope):
        _, _, case = _open(scope, store, client, contact_recovery=True)
        staff(scope, actor_email, case=case)
        record = _record(scope, store, client)
        plan = _plan(record)
        return {"state": plan["state"], "transition": plan["id"], "revision": record["revision"],
                "proposed_contacts": dict(plan["proposed"]), "reason": "complete_recorded_contact_recovery"}


def recover_transition(scope, store, client, proposed_contacts=None, *, actor_email):
    """Fresh own staff/ACL/lifecycle, exact contact-only payload, no stored role."""
    from .communication_consent import gate, staff, _open, finish_profile_change
    if proposed_contacts is not None and (not isinstance(proposed_contacts, dict) or not proposed_contacts or not set(proposed_contacts) <= {"email", "phone"}):
        raise ValueError("Recovery accepts only the exact proposed contact fields.")
    with gate(scope):
        _, current, case = _open(scope, store, client, contact_recovery=True)
        staff(scope, actor_email, case=case)
        plan = _plan(_record(scope, store, client))
        if plan.get("enrollment"):
            raise ValueError("Use explicit same-ID enrollment recovery for this operation.")
        supplied = normalize_profile(current | (proposed_contacts if proposed_contacts is not None else plan["proposed"]))
        proposed = normalize_profile(current | plan["proposed"])
        if (any(supplied[key] != proposed[key] for key in ("email", "phone"))
                or _hash(_contact_fields(current)) not in (plan["before"], plan["after"])):
            raise ValueError("Recovery payload no longer matches the recorded contact transition.")
        if plan["state"] == "pending":
            _denials(scope, client, plan)
            staff(scope, actor_email, case=case)
            with store._lock:
                _atomic(scope.check(store, client) / "profile.json", proposed)
            finish_profile_change(scope, store, client, True)
        result = finish_transition(scope, store, client, plan["id"])
        return dict(result, recovery_actor=actor_email)


def recover_enrollment(scope, store, client, *, actor_email):
    """Read exact own coordinator BEFORE inventory, with fresh current authority."""
    from .communication_consent import gate, staff
    with gate(scope):
        record = _record(scope, store, client)
        plan = _plan(record)
        if plan.get("enrollment") is not True:
            raise ValueError("No intact pending enrollment belongs to this identity.")
        snapshot = plan["profile_snapshot"]
        case = _lifecycle(scope, client, snapshot)
        staff(scope, actor_email, case=case)
        profile_path = scope.check(store, client) / "profile.json"
        if profile_path.exists() and _hash(store.profile(client)) != plan["profile_sha256"]:
            raise ValueError("Existing profile does not match the pending enrollment.")
        if not any(row.get("action") == "enrollment_recovery" and row.get("id") == plan["id"] and row.get("actor") == actor_email for row in record["history"]):
            record["history"].append({"action": "enrollment_recovery", "id": plan["id"], "revision": record["revision"], "actor": actor_email, "at": clock.stamp()})
            _save(scope, store, client, record)
        if plan["state"] == "pending":
            _denials(scope, client, plan)
            _lifecycle(scope, client, snapshot)
            staff(scope, actor_email, case=case)
            with store._lock:
                _atomic(profile_path, snapshot)
        result = finish_transition(scope, store, client, plan["id"])
        return dict(result, client=client, recovery_actor=actor_email)


def enrollment_recovery_view(scope, store, client, *, actor_email):
    """Own authorized proposal even before profile exists; no member metadata."""
    from .communication_consent import gate, staff
    with gate(scope):
        record = _record(scope, store, client)
        plan = _plan(record)
        if plan.get("enrollment") is not True:
            raise ValueError("No intact pending enrollment belongs to this identity.")
        case = _lifecycle(scope, client, plan["profile_snapshot"])
        staff(scope, actor_email, case=case)
        return {"state": plan["state"], "transition": plan["id"], "client": client, "revision": record["revision"],
                "proposed_profile": dict(plan["profile_snapshot"]), "reason": "complete_recorded_enrollment_recovery"}


def preflight_purge(scope, selected):
    """Raise before ANY destructive Q1 effect; pending dependencies survive."""
    from .communication_consent import gate
    with gate(scope):
        from .promotion import preflight_purge as promotion_preflight
        promotion_preflight(scope, selected)
        for identity in contacts.inventory(scope):
            ms, st = _composition(scope, identity.store_kind, readonly=True)
            record = _record(ms, st, identity.client)
            if (identity.store_kind, identity.client) in selected and (record.get("pending") or record.get("transition")):
                raise ValueError("Complete current-authorized contact recovery before purging this case.")
            if record.get("transition"):
                plan = _plan(record)
                if any((row["kind"], row["client"]) in selected for row in plan["affected"]):
                    raise ValueError("Complete current-authorized contact recovery before purging an affected case.")
