"""Installation-owned nonsecret Drive configuration; no provider calls.

Order for writes is sorted case locks, then settings OS lock. Historical
metadata never authorizes a write. Audit references are R2 event markers,
not signing certificates or statements about the ledger's daily anchors.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import stat

import clock
import events
import jobs
import oslock
import restricted
from . import sync
from .drive_intake import CASE_ID, REMOTE_ID, MAX_MAPPED_CASES, IntakeError, digest

HASH = re.compile(r"[0-9a-f]{64}")
ATTEMPT = re.compile(r"[0-9a-f]{32,64}")
STAGING = re.compile(r"settings\.json\.[0-9]+\.[0-9]+\.tmp")
MAX_BYTES = 2 * 1024 * 1024


class Conflict(IntakeError):
    pass


def paths(scope):
    scope.validate()
    folder = scope.data / "drive"
    for path in (folder, folder / "settings.json", folder / "settings.lock"):
        for part in (path, *path.parents):
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise IntakeError("The installation's Drive settings path is unavailable.") from exc
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise IntakeError("Drive settings need plain installation-owned paths.")
    return folder / "settings.json", folder / "settings.lock"


def provider(value):
    if not isinstance(value, dict) or set(value) != {"root_folder_id", "documents_subfolder"}:
        raise IntakeError("Choose the Drive root and documents subfolder explicitly.")
    root, sub = value["root_folder_id"], value["documents_subfolder"]
    if root is not None and (not isinstance(root, str) or not REMOTE_ID.fullmatch(root)):
        raise IntakeError("Invalid configured Drive root.")
    if sub is not None and (not isinstance(sub, str) or not sub.strip() or len(sub) > 200 or any(ord(c) < 32 for c in sub)):
        raise IntakeError("Invalid configured Drive documents subfolder.")
    return {"root_folder_id": root, "documents_subfolder": sub}


def bindings(value):
    if (not isinstance(value, dict) or len(value) > MAX_MAPPED_CASES
            or any(not isinstance(remote, str) or not REMOTE_ID.fullmatch(remote)
                   or not isinstance(case, str) or not CASE_ID.fullmatch(case) for remote, case in value.items())
            or len(set(value.values())) != len(value)):
        raise IntakeError("Invalid bounded Drive case mapping.")
    return dict(sorted(value.items()))


def _payload(actor, revision, supplied_provider, supplied_bindings):
    return digest({"actor": actor, "revision": revision, "provider": supplied_provider, "bindings": supplied_bindings})


def validate(value):
    if (not isinstance(value, dict) or set(value) != {"version", "revision", "provider", "intake_mapping", "changed", "operation"}
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["revision"]) is not int or value["revision"] < 1):
        raise IntakeError("The installation's Drive settings are damaged; no defaults replace them.")
    supplied_provider = provider(value["provider"])
    mapping = value["intake_mapping"]
    if (not isinstance(mapping, dict) or set(mapping) != {"version", "revision", "bindings"}
            or type(mapping["version"]) is not int or mapping["version"] != 1
            or type(mapping["revision"]) is not int or mapping["revision"] != value["revision"]):
        raise IntakeError("The installation's Drive mapping revision is damaged.")
    supplied_bindings = bindings(mapping["bindings"])
    if supplied_bindings and supplied_provider["root_folder_id"] is None:
        raise IntakeError("A mapped Drive intake needs an explicit configured root.")
    changed, operation = value["changed"], value["operation"]
    if (not isinstance(changed, dict) or set(changed) != {"actor", "name", "role", "via", "at"}
            or any(not isinstance(changed[k], str) or not changed[k].strip() or len(changed[k]) > 200 for k in ("actor", "name"))
            or changed["role"] not in events.ROLES or changed["via"] not in events.VIA
            or not isinstance(changed["at"], str) or clock.parse(changed["at"]) is None):
        raise IntakeError("The Drive settings change metadata is damaged.")
    if (not isinstance(operation, dict) or set(operation) != {"id", "payload_sha256", "previous_revision", "audit"}
            or not isinstance(operation["id"], str) or not ATTEMPT.fullmatch(operation["id"])
            or type(operation["previous_revision"]) is not int or operation["previous_revision"] != value["revision"] - 1
            or operation["payload_sha256"] != _payload(changed["actor"], operation["previous_revision"], supplied_provider, supplied_bindings)):
        raise IntakeError("The Drive settings operation proof is damaged.")
    audit = operation["audit"]
    if not isinstance(audit, dict) or audit.get("state") not in {"pending", "recorded"}:
        raise IntakeError("The Drive settings audit marker is damaged.")
    if audit["state"] == "pending" and set(audit) != {"state"}:
        raise IntakeError("The Drive settings audit marker is damaged.")
    if audit["state"] == "recorded" and (set(audit) != {"state", "hash", "at"}
            or not isinstance(audit["hash"], str) or not HASH.fullmatch(audit["hash"])
            or not isinstance(audit["at"], str) or clock.parse(audit["at"]) is None):
        raise IntakeError("The Drive settings audit marker is damaged.")
    return value


def read_record(scope):
    path, _ = paths(scope)
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise IntakeError("The installation's Drive settings cannot be read safely.") from exc
    try:
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise IntakeError("The installation's Drive settings cannot be read safely.")
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, IntakeError):
            raise
        raise IntakeError("The installation's Drive settings are unavailable or damaged; no defaults replace them.") from exc


def read(scope):
    """Only installation data can authorize mappings; shipped data cannot."""
    record = read_record(scope)
    if record is not None:
        return {**record["provider"], "results_folder_name": None, "intake_mapping": record["intake_mapping"]}
    defaults_path = scope.root / "schemas/registers/connectors.json"
    sync._plain(defaults_path)
    try:
        defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
        drive = defaults.get("google_drive") if isinstance(defaults, dict) else None
        if not isinstance(drive, dict):
            raise ValueError()
        supplied_provider = provider({"root_folder_id": drive.get("root_folder_id"), "documents_subfolder": drive.get("documents_subfolder")})
    except (OSError, ValueError, UnicodeError) as exc:
        raise IntakeError("The shipped Drive defaults are unavailable.") from exc
    return {**supplied_provider, "results_folder_name": None, "intake_mapping": None}


class Settings:
    def __init__(self, scope, actor_reader=None, may_access=restricted.visible_to):
        self.scope, self.actor_reader, self.may_access = scope, actor_reader, may_access

    def _actor(self, email):
        if events.base_path(self.scope.data).absolute() != self.scope.data / "events.jsonl":
            raise IntakeError("Drive settings need this installation's configured audit root.")
        if self.actor_reader is None:
            from review.auth import Accounts
            account_path = self.scope.data / "review_users.json"
            sync._plain(account_path)
            user = next((u for u in Accounts(account_path).users() if u["email"] == email), None)
        else:
            user = self.actor_reader(email)
        if (not isinstance(user, dict) or user.get("email") != email or user.get("active") is not True
                or user.get("role") != "attorney" or not isinstance(user.get("name"), str) or not user["name"].strip()):
            raise PermissionError("An active attorney account is required to configure Drive.")
        return user

    def _audit(self, record):
        if record["operation"]["audit"]["state"] == "recorded":
            return
        commitment = digest({k: v for k, v in record.items() if k != "operation"} | {
            "operation": {k: v for k, v in record["operation"].items() if k != "audit"}})
        what = f"Changed selected Drive settings (settings SHA-256 {commitment})"
        expected = {"kind": "settings", "action": "changed", "case": None, "who": record["changed"]["name"],
                    "role": record["changed"]["role"], "via": record["changed"]["via"], "what": what}
        found = [row for row in events.rows(events.base_path(self.scope.data)) if row.get("what") == what]
        if found:
            if len(found) != 1 or any(found[0].get(k) != v for k, v in expected.items()) or events.row_hash(found[0]) != found[0].get("hash"):
                raise IntakeError("The Drive settings audit marker cannot be reconciled safely.")
            row = found[0]
        else:
            row = events.record("settings", "changed", what, home=self.scope.data, who=record["changed"]["name"],
                                role=record["changed"]["role"], via=record["changed"]["via"])
        if row is None:
            return
        record["operation"]["audit"] = {"state": "recorded", "hash": row["hash"], "at": row["at"]}
        jobs._write(paths(self.scope)[0], record)

    @staticmethod
    def _view(record):
        return {"saved": True, "revision": record["revision"], "provider": record["provider"],
                "intake_mapping": record["intake_mapping"], "changed": record["changed"],
                "audit_pending": record["operation"]["audit"]["state"] != "recorded"}

    def view(self, email):
        """Full editing view requires current access to every mapped case."""
        actor = self._actor(email)
        record = read_record(self.scope)
        for case in record["intake_mapping"]["bindings"].values() if record else ():
            folder = self.scope.cases / case
            sync._plain(folder)
            if not folder.is_dir() or not self.may_access(actor, folder):
                raise PermissionError("The Drive configuration is unavailable to this account.")
        if record:
            return self._view(record)
        current = read(self.scope)
        return {"saved": False, "revision": 0, "provider": provider({k: current[k] for k in ("root_folder_id", "documents_subfolder")}),
                "intake_mapping": {"version": 1, "revision": 0, "bindings": {}}, "changed": None, "audit_pending": False}

    def retry_audit(self, email, expected_revision):
        """Explicit current-attorney repair of the saved operation only.

        Browser attempts and old authors need not remain available. Current
        authority grants only marker reconciliation, never a settings rewrite.
        """
        if type(expected_revision) is not int or expected_revision < 1:
            raise IntakeError("Use the displayed saved Drive settings revision to retry its audit.")
        self._actor(email)
        before = read_record(self.scope)
        if before is None:
            raise Conflict("No saved Drive settings audit is available; reload settings.")
        if before["revision"] != expected_revision:
            raise Conflict("Drive settings changed; reload their current revision.")
        cases = sorted(set(before["intake_mapping"]["bindings"].values()))
        with ExitStack() as locks:
            for case in cases:
                locks.enter_context(jobs.case_lock(self.scope.queue, case, timeout=0))
            _, lock = paths(self.scope)
            try:
                locks.enter_context(oslock.locked(lock, timeout=jobs.BUSY_WAIT))
            except oslock.LockBusy as exc:
                raise jobs.CaseBusy("Drive settings are being updated. Try again in a moment.") from exc
            actor = self._actor(email)
            current = read_record(self.scope)
            if current is None or current["revision"] != expected_revision:
                raise Conflict("Drive settings changed while waiting; reload their current revision.")
            for case in cases:
                folder = self.scope.cases / case
                sync._plain(folder)
                if not folder.is_dir() or not self.may_access(actor, folder):
                    raise PermissionError("The Drive configuration is unavailable to this account.")
            self._audit(current)
            return self._view(current)

    def save(self, email, *, expected_revision, operation_id, supplied_provider, supplied_bindings):
        if type(expected_revision) is not int or expected_revision < 0 or not isinstance(operation_id, str) or not ATTEMPT.fullmatch(operation_id):
            raise IntakeError("Use the displayed Drive revision and a stable settings attempt.")
        supplied_provider, supplied_bindings = provider(supplied_provider), bindings(supplied_bindings)
        if supplied_bindings and supplied_provider["root_folder_id"] is None:
            raise IntakeError("Configure a Drive root before mapping cases.")
        self._actor(email)
        before = read_record(self.scope)
        observed_revision = before["revision"] if before is not None else 0
        old_bindings = before["intake_mapping"]["bindings"] if before else {}
        cases = sorted(set(old_bindings.values()) | set(supplied_bindings.values()))
        payload = _payload(email, expected_revision, supplied_provider, supplied_bindings)
        with ExitStack() as locks:
            for case in cases:
                locks.enter_context(jobs.case_lock(self.scope.queue, case, timeout=0))
            path, lock = paths(self.scope)
            lock.parent.mkdir(parents=True, exist_ok=True)
            try:
                locks.enter_context(oslock.locked(lock, timeout=jobs.BUSY_WAIT))
            except oslock.LockBusy as exc:
                raise jobs.CaseBusy("Drive settings are being updated. Try again in a moment.") from exc
            actor = self._actor(email)
            for case in cases:
                folder = self.scope.cases / case
                sync._plain(folder)
                if folder.exists() and not self.may_access(actor, folder):
                    raise PermissionError("A configured case is unavailable to this account.")
            for case in supplied_bindings.values():
                self.scope.case(case)  # existing main case, lifecycle and source readiness
            current = read_record(self.scope)
            revision = current["revision"] if current is not None else 0
            if revision != observed_revision:
                raise Conflict("Drive settings changed while waiting; reload their current revision.")
            if current and current["operation"]["id"] == operation_id:
                if current["changed"]["actor"] != email or current["operation"]["payload_sha256"] != payload:
                    raise Conflict("The same Drive settings attempt cannot change its inputs or actor.")
                self._audit(current)
                return self._view(current)
            if revision != expected_revision:
                raise Conflict("Drive settings changed; reload their current revision.")
            if current and current["operation"]["audit"]["state"] != "recorded":
                raise Conflict("Complete the pending audit with Retry audit before saving another Drive settings change.")
            record = {"version": 1, "revision": revision + 1, "provider": supplied_provider,
                      "intake_mapping": {"version": 1, "revision": revision + 1, "bindings": supplied_bindings},
                      "changed": {"actor": email, "name": actor["name"], "role": "attorney", "via": "staff", "at": clock.stamp()},
                      "operation": {"id": operation_id, "payload_sha256": payload, "previous_revision": revision, "audit": {"state": "pending"}}}
            jobs._write(path, validate(record))
            self._audit(record)
            return self._view(record)


def purge_settings(home, ids):
    """Q1 trusted scope only; returns (removed bindings/staging files, unresolved).

    No provider call, account grant or whole-map disclosure. Only complete,
    attributable writes from this producer can be removed; damaged staging stays
    for an operator to resolve. Existing backups are handled by Q1 separately.
    """
    from .drive_intake import FirmScope
    home = Path(home).absolute()
    folder = home / "drive"
    try:
        folder.lstat()  # broken junctions must reach the no-reparse guard
    except FileNotFoundError:
        return 0, 0
    except OSError:
        return 0, 1
    scope = FirmScope(home.parent)
    if scope.data != home:
        raise IntakeError("Drive cleanup requires this installation's data root.")
    targets = {case for case in ids if isinstance(case, str) and CASE_ID.fullmatch(case)}
    removed, unresolved = 0, 0
    with ExitStack() as locks:
        for case in sorted(targets):
            locks.enter_context(jobs.case_lock(scope.queue, case, timeout=0))
        path, lock = paths(scope)
        locks.enter_context(oslock.locked(lock, timeout=jobs.BUSY_WAIT))
        controller = Settings(scope)
        canonical = None
        try:
            if events.base_path(home).absolute() != home / "events.jsonl":
                raise IntakeError("Drive cleanup requires this installation's audit root.")
            record = read_record(scope)
            canonical = record  # intact pre-cleanup durable operation, if present
            if record and record["operation"]["audit"]["state"] != "recorded":
                controller._audit(record)
                if record["operation"]["audit"]["state"] != "recorded":
                    raise IntakeError("Drive settings audit is still pending.")
            old = record["intake_mapping"]["bindings"] if record else {}
            kept = {remote: case for remote, case in old.items() if case not in targets}
            if len(kept) != len(old):
                revision = record["revision"]
                actor = "system:purge"
                acting = events.actor() or {}
                name = str(acting.get("who") or "Case retention cleanup").strip()[:200]
                role = acting.get("role") if acting.get("role") in events.ROLES else "system"
                via = acting.get("via") if acting.get("via") in events.VIA else "system"
                payload = _payload(actor, revision, record["provider"], kept)
                replacement = {"version": 1, "revision": revision + 1, "provider": record["provider"],
                               "intake_mapping": {"version": 1, "revision": revision + 1, "bindings": kept},
                               "changed": {"actor": actor, "name": name, "role": role, "via": via, "at": clock.stamp()},
                               "operation": {"id": payload, "payload_sha256": payload, "previous_revision": revision, "audit": {"state": "pending"}}}
                jobs._write(path, validate(replacement))
                removed += len(old) - len(kept)
                controller._audit(replacement)
                if replacement["operation"]["audit"]["state"] != "recorded":
                    unresolved += 1
        except (OSError, IntakeError):
            unresolved += 1
        for temporary in sorted(folder.glob("settings.json.*.tmp")):
            if not STAGING.fullmatch(temporary.name):
                unresolved += 1
                continue
            try:
                sync._plain(temporary)
                info = temporary.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                    raise IntakeError("Interrupted Drive settings are unavailable.")
                pending = validate(json.loads(temporary.read_text(encoding="utf-8")))
                if targets.intersection(pending["intake_mapping"]["bindings"].values()):
                    survivors = set(pending["intake_mapping"]["bindings"].values()) - targets
                    # Never discard another case's sole interrupted settings
                    # intent. Mixed writes need the same durable canonical
                    # operation (audit may have advanced after temp creation).
                    def operation_proof(value):
                        return {key: item for key, item in value["operation"].items() if key != "audit"}
                    preserved = canonical is not None and (
                        canonical["provider"] == pending["provider"]
                        and canonical["intake_mapping"] == pending["intake_mapping"]
                        and canonical["changed"] == pending["changed"]
                        and operation_proof(canonical) == operation_proof(pending))
                    if survivors and not preserved:
                        unresolved += 1
                        continue
                    temporary.unlink()
                    removed += 1
            except (OSError, IntakeError, ValueError, UnicodeError, TypeError, KeyError):
                unresolved += 1
    return removed, unresolved
