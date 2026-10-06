"""Explicit prospect retirement; no consent, control or delegated access transfer.

Own reciprocal records use the installation communication gate. Initial pilot
support requires real enrollment nonces, not reconstructed legacy identity.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
import json
import re
import secrets

import clock
from . import contact_transitions as access
from .queue_bridge import _atomic, _safe

FILE = "portal_promotion.json"
MAX_BYTES = 1024 * 1024
_EFFECT = ContextVar("portal_promotion_effect", default=None)


def _profile(scope, store, client):
    from .communication_consent import _read
    value = _read(_safe(scope.check(store, client) / "profile.json"))
    if not isinstance(value, dict) or value.get("id") != client:
        raise ValueError("Promotion profile is unavailable.")
    return value


def _identity(scope, store, client):
    profile = _profile(scope, store, client)
    stamp = profile.get("created_at")
    record = access._record(scope, store, client)
    first = next((row for row in record["history"] if row.get("action") == "enrollment_denied"), None)
    if (not isinstance(stamp, str) or clock.parse(stamp) is None or not first
            or not isinstance(first.get("id"), str) or not access.ID.fullmatch(first["id"])):
        raise ValueError("This legacy profile has no original enrollment proof; promotion is unsupported.")
    return access._hash([str(scope.portal), scope.kind, client, stamp, first["id"]])


def _member(scope, store, client):
    return {"kind": scope.kind, "client": client, "identity": _identity(scope, store, client)}


def _temps(scope, store, client):
    paths = [p for p in _safe(scope.check(store, client)).glob(FILE + ".*.tmp")
             if re.fullmatch(r"portal_promotion\.json\.[0-9a-f]{16}\.tmp", p.name)]
    if len(paths) > 100:
        raise ValueError("Promotion staging requires maintenance; nothing was discarded.")
    return [_safe(path) for path in paths]


def _record(scope, store, client, *, path=None, allow_temps=False):
    from .communication_consent import _read
    if not allow_temps and _temps(scope, store, client):
        raise ValueError("Promotion interrupted writes require current-authorized recovery.")
    path = _safe(path or scope.check(store, client) / FILE)
    value = _read(path)
    if value is None:
        return None
    if (path.stat().st_size > MAX_BYTES or type(value.get("version")) is not int or value["version"] != 1
            or value.get("store") != str(scope.portal) or value.get("client") != client
            or not isinstance(value.get("role"), str) or value["role"] not in ("source", "target")
            or not isinstance(value.get("state"), str) or value["state"] not in ("pending", "completed")
            or not isinstance(value.get("operation"), str) or not access.ID.fullmatch(value["operation"])
            or not isinstance(value.get("history"), list) or not value["history"]
            or any(not isinstance(row, dict) or row.get("action") not in ("prepared", "recovery", "completed")
                   or not isinstance(row.get("actor"), str)
                   or not row["actor"].strip() or not isinstance(row.get("at"), str)
                   or clock.parse(row["at"]) is None for row in value["history"])):
        raise ValueError("Promotion record is damaged.")
    for key, kind in (("source", "prospect"), ("target", "client")):
        member = value.get(key)
        if (not isinstance(member, dict) or set(member) != {"kind", "client", "identity"}
                or member["kind"] != kind or not isinstance(member["client"], str)
                or not access.contacts.CLIENT_ID.fullmatch(member["client"])
                or not isinstance(member["identity"], str) or not re.fullmatch(r"[0-9a-f]{64}", member["identity"])):
            raise ValueError("Promotion membership is damaged.")
    own = value[value["role"]]
    if (own["kind"], own["client"]) != (scope.kind, client):
        raise ValueError("Promotion ownership is damaged.")
    proposed = value.get("proposed")
    if (not isinstance(proposed, dict) or set(proposed) != {"email", "phone"}
            or access.normalize_profile(proposed) != proposed
            or not isinstance(value.get("closure_on"), str)
            or not isinstance(value.get("started_at"), str) or clock.parse(value["started_at"]) is None
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["closure_on"])
            or value.get("plan_sha256") != access._hash(_plan(value))):
        raise ValueError("Promotion proposal is damaged.")
    if "new_target" in value:
        _reservation(scope, value)
    if "restriction_initialized" in value and type(value["restriction_initialized"]) is not bool:
        raise ValueError("New-target restriction attribution is damaged.")
    return value


def _plan(record):
    keys = ("operation", "source", "target", "proposed", "closure_on", "started_at")
    return {k: record.get(k) for k in keys} | ({"new_target": record["new_target"]} if "new_target" in record else {})


def _save(scope, store, client, value):
    if len(json.dumps(value).encode()) > MAX_BYTES:
        raise ValueError("Promotion history requires maintenance; nothing was discarded.")
    _atomic(scope.check(store, client) / FILE, value)


def _resolve(scope, member, *, readonly=False):
    ms, st = access._composition(scope, member["kind"], readonly=readonly)
    return ms, st, member["client"]


def _raw_records(scope):
    import read_scope
    return read_scope.contact_once(("promotion-records", str(scope.root)), lambda: _raw_records_read(scope), copy=True)


def _raw_records_read(scope):
    rows = access.contacts._raw_inventory(scope)
    records = []
    for row in rows:
        ms, st = access._composition(scope, row.store_kind, readonly=True)
        record = _record(ms, st, row.client)
        if record:
            records.append(record)
    return rows, records


def _completed(scope, record):
    """Immutable identity/association; current target contacts may change safely."""
    import prospects
    from .communication_consent import _read
    ts, target, cid = _resolve(scope, record["target"], readonly=True)
    if _identity(ts, target, cid) != record["target"]["identity"]:
        raise ValueError("Promotion target identity changed.")
    peer = _record(ts, target, cid)
    if (not peer or peer["state"] != "completed" or _plan(peer) != _plan(record)
            or _profile(ts, target, cid).get("prospect") != record["source"]["client"]):
        raise ValueError("Promotion target association is damaged.")
    ss, source, pid = _resolve(scope, record["source"], readonly=True)
    path = _safe(source.client_dir(pid))
    if not path.exists():
        # Own target receipt is historical completion, never authority over a
        # deleted/recreated source. Strict raw inventory refuses partial dirs.
        return
    peer = _record(ss, source, pid)
    if _identity(ss, source, pid) != record["source"]["identity"]:
        if record["role"] == "target" and peer is None:
            # A separately enrolled identity after source-only Q1 is ordinary
            # inventory. It inherits neither retirement nor access authority.
            return
        raise ValueError("Promotion source identity changed.")
    became = (_read(_safe(ss.cases / pid / prospects.FILE), {}) or {}).get("became_client")
    if (not peer or peer["state"] != "completed" or _plan(peer) != _plan(record)
            or _profile(ss, source, pid).get("closed_on") != record["closure_on"]
            or not isinstance(became, dict) or became.get("id") != cid
            or became.get("operation") != record["operation"]
            or became.get("target_identity") != record["target"]["identity"]):
        raise ValueError("Promotion source retirement is damaged.")


def eligible_inventory(scope, rows):
    """Never turn a damaged retirement into an ordinary active identity."""
    excluded = set()
    try:
        for row in rows:
            ms, st = access._composition(scope, row.store_kind, readonly=True)
            record = _record(ms, st, row.client)
            if record and record["state"] == "completed":
                # A counterpart can still be pending after first final write.
                peer_key = "target" if record["role"] == "source" else "source"
                ps, peer_store, pid = _resolve(scope, record[peer_key], readonly=True)
                peer = _record(ps, peer_store, pid)
                if peer and peer["state"] == "pending" and _plan(peer) == _plan(record):
                    continue
                _completed(scope, record)
                if record["role"] == "source":
                    excluded.add((row.store_kind, row.client))
    except (ValueError, OSError, LookupError, TypeError, KeyError):
        raise access.contacts.InventoryError("promotion_recovery_required") from None
    return tuple(row for row in rows if (row.store_kind, row.client) not in excluded)


def assert_ready(scope, store, client):
    _, records = _raw_records(scope)
    for record in records:
        members = _current_members(scope, record)
        if (scope.kind, client) in members:
            if record["state"] != "completed":
                raise ValueError("Complete pending promotion recovery before access.")
            _completed(scope, record)
            if (scope.kind, client) == (record["source"]["kind"], record["source"]["client"]):
                raise ValueError("This prospect's portal was retired by explicit promotion.")


def guard_mutation(scope, store, client, proposed):
    _, records = _raw_records(scope)
    for record in records:
        if (scope.kind, client) not in _current_members(scope, record):
            continue
        if _EFFECT.get() == (str(scope.root), record["operation"]):
            continue
        if record["state"] == "pending" or scope.kind == "prospect":
            raise ValueError("Explicit promotion recovery is required before profile mutation.")
        _completed(scope, record)
        if (proposed.get("id") != client or proposed.get("created_at") != _profile(scope, store, client).get("created_at")
                or proposed.get("prospect") != record["source"]["client"]):
            raise ValueError("Completed promotion identity/association cannot be overwritten.")


def preflight_purge(scope, selected):
    _, records = _raw_records(scope)
    for record in records:
        members = _current_members(scope, record)
        if members & selected:
            if record["state"] != "completed":
                raise ValueError("Recover pending promotion before deleting either identity.")
            _completed(scope, record)
            # Main-case purge normally includes its explicitly associated
            # prospect. Do not leave a surviving source with deleted peer proof.
            source = ("prospect", record["source"]["client"])
            target = ("client", record["target"]["client"])
            ss, st, pid = _resolve(scope, record["source"], readonly=True)
            if target in selected and source not in selected and source in members:
                raise ValueError("Include the associated retired prospect in this target purge.")


def _current_members(scope, record):
    """Completed receipts bind original enrollment identities, never ID reuse."""
    members = {(record[k]["kind"], record[k]["client"]) for k in ("source", "target")}
    if record["state"] != "completed":
        return members  # Pending/mismatched proposals continue to deny by ID.
    _completed(scope, record)  # Damaged/retained mismatched peer remains held.
    ss, st, pid = _resolve(scope, record["source"], readonly=True)
    if (not _safe(st.client_dir(pid)).exists()
            or _identity(ss, st, pid) != record["source"]["identity"]):
        members.discard(("prospect", pid))
    return members


def associated_prospects(scope, store, client, candidates):
    """Filter historical bare-ID associations through the completed receipt."""
    from .communication_consent import gate
    with gate(scope):
        record = _record(scope, store, client)
        if record is None or record["state"] != "completed":
            return sorted(set(candidates))  # Pending Q1 is refused by preflight.
        if record["role"] != "target":
            return []
        members = _current_members(scope, record)
        pid = record["source"]["client"]
        return [pid] if pid in candidates and ("prospect", pid) in members else []


@contextmanager
def _effects(scope, operation):
    token = _EFFECT.set((str(scope.root), operation))
    try:
        yield
    finally:
        _EFFECT.reset(token)


def _authority(scope, record, actor_email, *, source_only=False):
    from .communication_consent import staff
    from case_assignment import Assignments
    import jobs
    import prospects
    from .communication_consent import _read
    actor = None
    for key in ("source", "target"):
        if source_only and key == "target":
            continue
        ms, st, client = _resolve(scope, record[key])
        if _identity(ms, st, client) != record[key]["identity"]:
            raise ValueError("Promotion identity no longer matches the recorded operation.")
        case = _safe(ms.cases / client)
        if not case.is_dir():
            raise ValueError("Promotion case folder is unavailable.")
        Assignments(ms.cases, jobs.folder_for(ms.cases), lambda: [])._open(case)
        actor = staff(ms, actor_email, case=case)
        profile = _profile(ms, st, client)
        if key == "target":
            access._lifecycle(ms, client, profile)
            if record["state"] == "pending":
                current = access.normalize_profile(profile)
                if any(current[k] not in ("", record["proposed"][k]) for k in ("email", "phone")):
                    raise ValueError("Target contacts no longer match the exact promotion proposal.")
                if profile.get("prospect") not in (None, record["source"]["client"]):
                    raise ValueError("Target prospect association changed before recovery.")
        else:
            rec = _read(_safe(case / prospects.FILE))
            if (not isinstance(rec, dict) or rec.get("id") != client or rec.get("declined")
                    or profile.get("declined_on") or profile.get("destroyed")
                    or profile.get("status") in {"closed", "declined", "destroyed", "purged"}):
                raise ValueError("The prospect is unavailable for promotion recovery.")
            closed = profile.get("closed_on")
            became = rec.get("became_client")
            own_association = (isinstance(became, dict) and became.get("operation") == record["operation"]
                               and became.get("id") == record["target"]["client"]
                               and became.get("target_identity") == record["target"]["identity"])
            if (closed and (closed != record["closure_on"] or not own_association)) or (became and not own_association):
                raise ValueError("An unrelated closed prospect cannot recover this promotion.")
            current = access.normalize_profile(profile)
            if {k: current[k] for k in ("email", "phone")} != record["proposed"]:
                raise ValueError("Recorded prospect contacts changed before promotion recovery.")
    return actor


def _confidentiality(scope, record):
    """A copy cannot widen source access, including staff other than its actor."""
    import restricted
    from .communication_consent import _read
    ss, source, pid = _resolve(scope, record["source"], readonly=True)
    ts, target, cid = _resolve(scope, record["target"], readonly=True)
    source_case, target_case = _safe(ss.cases / pid), _safe(ts.cases / cid)
    if not restricted.is_restricted(source_case):
        return  # Ordinary source may be copied into a narrower protected target.
    if not restricted.is_restricted(target_case):
        raise ValueError("Promotion is held: an attorney must protect the target's access before copying restricted prospect data.")
    audiences = []
    for case in (source_case, target_case):
        data = _read(_safe(case / "access.json"), {})
        people = data.get("people", [])
        if (not isinstance(people, list) or any(not isinstance(row, dict) or not isinstance(row.get("email"), str) for row in people)):
            raise ValueError("Promotion confidentiality permissions are damaged.")
        audiences.append({row["email"].strip().casefold() for row in people})
    if not audiences[1] <= audiences[0]:
        raise ValueError("Promotion is held: the target's named staff access is broader than the current restricted prospect. An attorney must review permissions.")
    if restricted.messages_allowed(target_case) and not restricted.messages_allowed(source_case):
        raise ValueError("Promotion is held: the target's automatic-message policy is broader than the restricted prospect. An attorney must review message safety.")


def _reservation(scope, record):
    """Pure exact snapshot parser; no legacy pair changes or nonce backfill."""
    value = record.get("new_target")
    if (not isinstance(value, dict) or set(value) != {"profile_snapshot", "profile_sha256", "enrollment_id", "conflict_search", "conflict_decision"}
            or not isinstance(value.get("enrollment_id"), str) or not access.ID.fullmatch(value["enrollment_id"])):
        raise ValueError("New-target reservation is damaged.")
    snapshot = value["profile_snapshot"]
    base = {"id", "name", "email", "phone", "language", "consent", "status", "created_at"}
    if (not isinstance(snapshot, dict) or not base <= set(snapshot)
            or snapshot.get("id") != record["target"]["client"] or snapshot.get("status") != "invited"
            or not isinstance(snapshot.get("name"), str) or not 2 <= len(snapshot["name"].strip()) <= 200
            or snapshot.get("language") not in ("en", "pt", "es", "ht")
            or snapshot.get("email") != "" or snapshot.get("phone") != ""
            or snapshot.get("consent") != {channel: False for channel in sorted(access.contacts.CHANNELS)}
            or not isinstance(snapshot.get("created_at"), str) or clock.parse(snapshot["created_at"]) is None
            or access._hash(snapshot) != value["profile_sha256"]):
        raise ValueError("New-target profile snapshot is damaged.")
    access.initial_fields({k: v for k, v in snapshot.items() if k not in base})
    target_portal = scope.data / "portal"
    identity = access._hash([str(target_portal), "client", snapshot["id"], snapshot["created_at"], value["enrollment_id"]])
    search, decision = value["conflict_search"], value["conflict_decision"]
    if (identity != record["target"]["identity"] or not isinstance(search, dict) or not isinstance(decision, dict)
            or not isinstance(search.get("id"), str) or decision.get("search") != search["id"]
            or decision.get("decision") not in ("none", "waived", "undecided")):
        raise ValueError("New-target identity/conflict reservation is damaged.")
    return value


def allocation_guard(scope, *, reservation=None):
    """Ordinary allocation cannot race or reuse a pending reservation's ID."""
    _, records = _raw_records(scope)
    for record in records:
        if record["state"] == "pending" and (reservation is None or _plan(record) != _plan(reservation)):
            raise ValueError("Recover the exact pending promotion before allocating another identity.")


def reserved_enrollment(scope, store, client, snapshot, record):
    """Private enrollment seam validates canonical source, never supplied data alone."""
    ss, source, pid = _resolve(scope, record["source"], readonly=True)
    current = _record(ss, source, pid)
    if (current is None or current["state"] != "pending" or _plan(current) != _plan(record)
            or _identity(ss, source, pid) != current["source"]["identity"]
            or scope.kind != "client" or client != current["target"]["client"]):
        raise ValueError("Exact current source reservation is required for private enrollment.")
    value = _reservation(scope, current)
    if snapshot != value["profile_snapshot"]:
        raise ValueError("Enrollment no longer matches the immutable reserved snapshot.")
    allocation_guard(scope, reservation=current)
    return value["enrollment_id"]


def _case_reservation(scope, store, client, record):
    """Attribute target folder before restriction/conflict/enrollment effects."""
    case = _safe(scope.cases / client)
    path = case / FILE
    wanted = dict(record, store=str(scope.portal), client=client, role="target", state="pending")
    if case.exists() and not path.exists():
        residue = list(case.iterdir())
        if any(not re.fullmatch(r"portal_promotion\.json\.[0-9a-f]{16}\.tmp", p.name) for p in residue):
            raise ValueError("Reserved target folder has unowned content; recovery is held.")
        for tmp in residue:
            staged = _record(scope, store, client, path=_safe(tmp), allow_temps=True)
            if staged is None or _plan(staged) != _plan(record):
                raise ValueError("Reserved target staging ownership is unresolved.")
    existing = _record(scope, store, client, path=path, allow_temps=True)
    if existing is not None and _plan(existing) != _plan(record):
        raise ValueError("Reserved target case identity belongs to another operation.")
    if existing is not None and not _safe(store.client_dir(client) / "profile.json").exists():
        import conflicts
        allowed = {FILE, "access.json", "access.json.tmp", conflicts.FILE, conflicts.FILE + ".tmp"}
        if any(p.name not in allowed and not re.fullmatch(r"portal_promotion\.json\.[0-9a-f]{16}\.tmp", p.name) for p in case.iterdir()):
            raise ValueError("Unenrolled reserved target has unowned case content; recovery is held.")
    if existing is None:
        _atomic(path, wanted)  # Never rewrite retained reservation/history on retry.
    for tmp in case.glob(FILE + ".*.tmp"):
        if not re.fullmatch(r"portal_promotion\.json\.[0-9a-f]{16}\.tmp", tmp.name):
            continue
        staged = _record(scope, store, client, path=_safe(tmp), allow_temps=True)
        if staged is None or _plan(staged) != _plan(record):
            raise ValueError("Reserved target staging ownership is unresolved.")
        _safe(tmp).unlink()


def _ensure_new_target(scope, record, actor_email):
    """Same-ID reservation recovery composes the accepted enrollment protocol."""
    import conflicts
    import restricted
    from .communication_consent import staff
    reservation = _reservation(scope, record)
    actor = _authority(scope, record, actor_email, source_only=True)
    ts, target, cid = _resolve(scope, record["target"])
    ss, source, pid = _resolve(scope, record["source"])
    case = _safe(ts.cases / cid)
    profile_path = _safe(target.client_dir(cid) / "profile.json")
    if profile_path.exists() and _identity(ts, target, cid) != record["target"]["identity"]:
        raise ValueError("Reserved target enrollment identity was replaced.")
    # Once a canonical folder exists its CURRENT permission wins, before even
    # reservation publication. A source restriction never overwrites a later
    # target ACL change on recovery.
    staff(ts, actor_email, case=case)
    _case_reservation(ts, target, cid, record)
    marker = _record(ts, target, cid, path=case / FILE, allow_temps=True)
    # Existing marked target must never be reopened or have current ACL erased.
    from review.front_desk import TRACKS
    track = reservation["profile_snapshot"].get("track")
    found = restricted.kind_law(track) if track else None
    if marker.get("restriction_initialized") and not (case / "access.json").exists() and (restricted.is_restricted(ss.cases / pid) or found):
        raise ValueError("Reserved target restriction was removed; an attorney must review access before recovery.")
    if not marker.get("restriction_initialized") and not (case / "access.json").exists():
        if found:
            restricted.protect_new(case, found, dict(TRACKS)[track], "Added as", actor["name"])
        restricted.carry_over(ss.cases / pid, case, actor["name"])
    staff(ts, actor_email, case=case)
    if not marker.get("restriction_initialized"):
        _atomic(case / FILE, dict(marker, restriction_initialized=True))
    search, decision = reservation["conflict_search"], reservation["conflict_decision"]
    conflicts._check(decision["decision"], str(decision.get("reason") or ""), actor["role"], search,
                     lambda other: restricted.visible_to(actor, _safe(ts.cases / other)), allow_undecided=True)
    if not (case / conflicts.FILE).exists():
        conflicts.record_new(case, search, decision)
    else:
        current = conflicts.record_of(case) or {}
        if current.get("decision") != decision:
            raise ValueError("Reserved target conflict decision changed; recovery is held.")
    _authority(scope, record, actor_email, source_only=True)
    staff(ts, actor_email, case=case)
    current = access._record(ts, target, cid)
    if current.get("transition"):
        plan = access._plan(current)
        if (plan.get("enrollment") is not True or plan["id"] != reservation["enrollment_id"]
                or plan["profile_snapshot"] != reservation["profile_snapshot"]):
            raise ValueError("Reserved target enrollment was replaced.")
        access.recover_enrollment(ts, target, cid, actor_email=actor_email)
    elif not profile_path.exists():
        if target.client_dir(cid).exists():
            raise ValueError("Reserved target portal residue requires exact enrollment recovery.")
        identity, snapshot = access.prepare_enrollment(ts, target, cid, reservation["profile_snapshot"], _promotion=record)
        _authority(scope, record, actor_email, source_only=True)
        staff(ts, actor_email, case=case)
        with target._lock:
            target._write(profile_path, snapshot)
            target._finish_contact_change(cid, {"contact": identity, "consent": False})
    _authority(scope, record, actor_email)


def create_target(scope, store, prospect, body, *, actor_email):
    """Protected new-target wrapper, exact source contacts and normal conflicts."""
    from .communication_consent import gate, staff
    from .bank import language_code
    from .store import _now
    from review import front_desk
    import conflicts
    import offices
    import restricted
    with gate(scope):
        if scope.kind != "client" or not isinstance(body, dict):
            raise ValueError("New-target promotion requires the explicit main store.")
        ss, source = access._composition(scope, "prospect")
        ss.check(source, prospect)
        case = access._lifecycle(ss, prospect, source.profile(prospect))
        actor = staff(ss, actor_email, case=case)
        source_member = _member(ss, source, prospect)
        access.current_revision(ss, source, prospect)
        allocation_guard(scope)
        current = source.profile(prospect)
        proposed = {k: access.normalize_profile(current)[k] for k in ("email", "phone")}
        if proposed["email"] and any(row.email == proposed["email"] and (row.store_kind, row.client) != ("prospect", prospect)
                                      for row in access.contacts.inventory(scope)):
            raise ValueError("Promotion contact remains ambiguous and requires authorized office review.")
        supplied = access.normalize_profile({k: body.get(k, current.get(k, "")) for k in ("email", "phone")})
        if supplied != proposed:
            raise ValueError("Promotion requires the exact current prospect contacts; change them separately first.")
        name = " ".join(str(body.get("name") or current.get("name") or "").split())
        language = language_code(str(body.get("language") or current.get("language") or "pt"))
        filing = str(body.get("filing") or "i485")
        office, track = str(body.get("office") or "").strip(), str(body.get("track") or "").strip()
        if not 2 <= len(name) <= 200 or language is None or filing not in {q["id"] for q in front_desk.questionnaires()}:
            raise ValueError("Choose a valid client name, language and questionnaire.")
        if office and offices.by_id(office) is None:
            raise ValueError("Choose the office from the list.")
        if track and track not in dict(front_desk.TRACKS):
            raise ValueError("Choose the case's track from the list.")
        search, decision = conflicts.for_new_client(scope.cases, body.get("conflict"), name, by=actor["name"], role=actor["role"],
                                                   may_see=lambda other: restricted.visible_to(actor, _safe(scope.cases / other)))
        if decision["decision"] == "declined":
            conflicts._log_decision(scope.cases, decision, None, "add")
            return {"declined": True, "name": name, "note": conflicts.DECLINED}
        cid = front_desk.new_client_id(store, scope.cases, name)
        # This reservation never takes over a legacy/orphan folder.
        base, n = cid, 2
        while _safe(scope.cases / cid).exists() or _safe(store.client_dir(cid)).exists():
            cid, n = f"{base}-{n}", n + 1
        now = _now().isoformat()
        snapshot = {"id": cid, "name": name, "email": "", "phone": "", "language": language,
                    "consent": {channel: False for channel in sorted(access.contacts.CHANNELS)}, "status": "invited", "created_at": now,
                    "filing": filing, "added_by": actor["name"], "added_at": now,
                    **({"office": office} if office else {}), **({"track": track} if track else {})}
        nonce = secrets.token_hex(16)
        target_member = {"kind": "client", "client": cid, "identity": access._hash([str(scope.portal), "client", cid, now, nonce])}
        record = {"version": 1, "store": str(ss.portal), "client": prospect, "role": "source", "state": "pending",
                  "operation": secrets.token_hex(16), "source": source_member, "target": target_member, "proposed": proposed,
                  "closure_on": clock.today().isoformat(), "started_at": clock.stamp(),
                  "history": [{"action": "prepared", "actor": actor_email, "at": clock.stamp()}],
                  "new_target": {"profile_snapshot": snapshot, "profile_sha256": access._hash(snapshot), "enrollment_id": nonce,
                                 "conflict_search": search, "conflict_decision": decision}}
        record["plan_sha256"] = access._hash(_plan(record))
        _reservation(scope, record)
        _authority(scope, record, actor_email, source_only=True)
        _save(ss, source, prospect, record)  # durable denial precedes target mkdir
        _ensure_new_target(scope, record, actor_email)
        result = _finish(scope, record, actor_email)
        restricted_now = restricted.is_restricted(scope.cases / cid)
        return {"id": cid, "name": name, "filing": filing, "from_call": result["carried"],
                "promotion": {key: result[key] for key in ("state", "operation", "client")},
                **({"restricted": True, "note": restricted.INVITE_HELD} if restricted_now else {}),
                **({"held": True, "held_note": conflicts.HELD} if decision["decision"] == "undecided" else {})}


def promote(scope, store, client, prospect, *, actor_email):
    """Existing protected-created pair only; fresh current authority over both."""
    from .communication_consent import gate
    import prospects
    with gate(scope):
        if scope.kind != "client":
            raise ValueError("Promotion target must be the explicit main store.")
        scope.check(store, client)
        ss, source = access._composition(scope, "prospect")
        ss.check(source, prospect)
        if _record(ss, source, prospect) or _record(scope, store, client):
            raise ValueError("Existing promotion requires explicit same-operation recovery.")
        rows = access.contacts.inventory(scope)
        access.current_revision(scope, store, client)
        access.current_revision(ss, source, prospect)
        access._lifecycle(scope, client, store.profile(client))
        access._lifecycle(ss, prospect, source.profile(prospect))
        rec = prospects.read(_safe(ss.cases / prospect))
        if rec.get("id") != prospect:
            raise ValueError("An actual prospect record is required.")
        proposed = {k: access.normalize_profile(source.profile(prospect))[k] for k in ("email", "phone")}
        target_contacts = access.normalize_profile(store.profile(client))
        if store.profile(client).get("prospect"):
            raise ValueError("An existing target prospect association requires separate review.")
        if any(target_contacts[k] not in ("", proposed[k]) for k in ("email", "phone")):
            raise ValueError("Different existing target contacts require separate review, not automatic promotion.")
        if proposed["email"] and any(row.email == proposed["email"] and (row.store_kind, row.client) not in {("prospect", prospect), ("client", client)} for row in rows):
            raise ValueError("Promotion contact remains ambiguous and requires authorized office review.")
        record = {"version": 1, "store": str(ss.portal), "client": prospect, "role": "source", "state": "pending",
                  "operation": secrets.token_hex(16), "source": _member(ss, source, prospect), "target": _member(scope, store, client),
                  "proposed": proposed, "closure_on": clock.today().isoformat(), "started_at": clock.stamp(),
                  "history": [{"action": "prepared", "actor": actor_email, "at": clock.stamp()}]}
        record["plan_sha256"] = access._hash(_plan(record))
        _authority(scope, record, actor_email)  # before first durable coordinator
        _confidentiality(scope, record)
        _save(ss, source, prospect, record)
        return _finish(scope, record, actor_email)


def recover(scope, store, client, operation, *, actor_email):
    from .communication_consent import gate
    with gate(scope):
        record = _record(scope, store, client, allow_temps=True)
        if record is None:
            candidates = [_record(scope, store, client, path=path, allow_temps=True) for path in _temps(scope, store, client)]
            if not candidates or any(not row or row["operation"] != operation or _plan(row) != _plan(candidates[0]) for row in candidates):
                raise ValueError("Intact own promotion proposal is unavailable; staging remains unresolved.")
            record = candidates[0]
        if not record or record["operation"] != operation:
            raise ValueError("Exact current promotion operation is required.")
        if "new_target" in record and record["state"] != "completed":
            # Initial temp-only source recovery must first establish the own
            # durable coordinator before private target enrollment is possible.
            _authority(scope, record, actor_email, source_only=True)
            ss, source, pid = _resolve(scope, record["source"])
            _save(ss, source, pid, dict(record, store=str(ss.portal), client=pid, role="source", state="pending"))
            _ensure_new_target(scope, record, actor_email)
        actor = _authority(scope, record, actor_email)
        if record["state"] == "completed":
            # A source-first completed write can precede target completion.
            ts, target, cid = _resolve(scope, record["target"])
            peer = _record(ts, target, cid, allow_temps=True)
            if peer and peer["state"] == "completed" and not _temps(scope, store, client) and not _temps(ts, target, cid):
                _completed(scope, record)
                return {"state": "completed", "operation": operation, "client": cid}
        record = dict(record, history=record["history"] + [{"action": "recovery", "actor": actor["email"], "at": clock.stamp()}])
        return _finish(scope, record, actor_email)


def recovery_view(scope, store, client, *, actor_email):
    """Protected exact operation handle; no client-selected roots or authority."""
    from .communication_consent import gate, staff
    from case_assignment import Assignments
    import jobs
    with gate(scope):
        record = _record(scope, store, client, allow_temps=True)
        if record is None:
            staged = [_record(scope, store, client, path=p, allow_temps=True) for p in _temps(scope, store, client)]
            if not staged or any(row is None or _plan(row) != _plan(staged[0]) for row in staged):
                raise ValueError("Intact own promotion proposal is unavailable; staging remains unresolved.")
            record = staged[0]
        if "new_target" in record and record["state"] != "completed":
            _authority(scope, record, actor_email, source_only=True)
            ts, target, cid = _resolve(scope, record["target"])
            case = _safe(ts.cases / cid)
            if case.exists():
                Assignments(ts.cases, jobs.folder_for(ts.cases), lambda: [])._open(case)
            staff(ts, actor_email, case=case)
            if _safe(target.client_dir(cid) / "profile.json").exists() and _identity(ts, target, cid) != record["target"]["identity"]:
                raise ValueError("Reserved target enrollment identity was replaced.")
        else:
            _authority(scope, record, actor_email)
        return {"state": record["state"], "operation": record["operation"],
                "source": record["source"]["client"], "client": record["target"]["client"],
                "new_target": "new_target" in record, "can_recover": record["state"] == "pending",
                "reason": "complete_exact_promotion_recovery"}


def _finish(scope, record, actor_email):
    from .communication_consent import _revoke
    import prospects
    actor = _authority(scope, record, actor_email)
    ss, source, pid = _resolve(scope, record["source"])
    ts, target, cid = _resolve(scope, record["target"])
    source_record = dict(record, store=str(ss.portal), client=pid, role="source", state="pending")
    target_record = dict(record, store=str(ts.portal), client=cid, role="target", state="pending")
    for ms, st, client, value in ((ss, source, pid, source_record), (ts, target, cid, target_record)):
        existing = _record(ms, st, client, allow_temps=True)
        if existing and _plan(existing) != _plan(record):
            raise ValueError("Promotion counterpart operation changed.")
        _save(ms, st, client, value)
    _authority(scope, record, actor_email)
    # Exact attributed interrupted writes only, after canonical denial exists
    # on both sides. A conflicting/unreadable temp remains unresolved.
    for ms, st, client in ((ss, source, pid), (ts, target, cid)):
        for path in _temps(ms, st, client):
            staged = _record(ms, st, client, path=path, allow_temps=True)
            if not staged or _plan(staged) != _plan(record):
                raise ValueError("Promotion staging ownership is unresolved.")
            path.unlink()
    for ms, st, client in ((ss, source, pid), (ts, target, cid)):
        _revoke(ms, st, client, sorted(access.contacts.CHANNELS), actor_email=actor_email,
                operation_id="promotion-" + record["operation"], operation_source="promotion")
    actor = _authority(scope, record, actor_email)  # before carry/publication
    _confidentiality(scope, record)  # Every retry rechecks non-actor staff too.
    with _effects(scope, record["operation"]):
        carried = prospects._carry_into_client(ts.cases, ts.portal, pid, cid, target, actor["name"], actor["role"])
        wanted = dict(record["proposed"], prospect=pid)
        if any(target.profile(cid).get(k) != value for k, value in wanted.items()):
            # This coordinator owns the exact contact copy. Both identities
            # are already durably denied and fully revoked. Creating another
            # contact coordinator here would strand recovery after a crash.
            # Normal later edits still use the ordinary transition protocol.
            _authority(scope, record, actor_email)
            with target._lock:
                profile = dict(target.profile(cid), **wanted)
                target._write(target.client_dir(cid) / "profile.json", profile)
        # Current actor is rechecked before final association/closure effects.
        actor = _authority(scope, record, actor_email)
        rec = prospects.read(ss.cases / pid)
        if not rec.get("became_client"):
            rec["became_client"] = {"id": cid, "by": actor["name"], "at": record["started_at"],
                                    "operation": record["operation"], "target_identity": record["target"]["identity"]}
            prospects._save(ss.cases / pid, rec, "became_client", "The prospect became a client under explicit promotion", actor["name"], actor["role"])
        if source.profile(pid).get("closed_on") != record["closure_on"]:
            source.update_profile(pid, closed_on=record["closure_on"])
    _authority(scope, record, actor_email)  # before releasing either pending marker
    if "new_target" in record:
        _atomic(ts.cases / cid / FILE, dict(target_record, state="completed", restriction_initialized=True,
                history=record["history"] + [{"action": "completed", "actor": actor_email, "at": clock.stamp()}]))
    for ms, st, client, value in ((ss, source, pid, source_record), (ts, target, cid, target_record)):
        value.update(state="completed", history=record["history"] + [{"action": "completed", "actor": actor_email, "at": clock.stamp()}])
        _save(ms, st, client, value)  # target releases last; partial pair remains held
    _completed(scope, value)
    return {"state": "completed", "operation": record["operation"], "client": cid, "carried": carried}
