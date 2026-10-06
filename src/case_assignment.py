"""Case responsibility, separate from access and from task/deadline ownership.

Lock order: existing jobs case lock, then existing events ledger lock. Callers
must derive actor_email from an authenticated session, never a request body.
The accounts callback and ACL callback are reread under the case lock. They do
not form a transaction with account administration or restriction changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Callable

import clock
import events
import jobs
import restricted

FILE = "case_assignment.json"
PART = FILE + ".part"
VERSION = 1
KIND = "assignment"  # additive R2 kind registration belongs to integration
ROLES = ("attorney", "paralegal")
MAX_BYTES = 4 * 1024 * 1024
MAX_HISTORY = 4096  # stop rather than discard retry/audit history
OPERATION = re.compile(r"[0-9a-f]{32,64}")
HASH = re.compile(r"[0-9a-f]{64}")
WORDS = {"claim": "Claimed case responsibility", "reassign": "Reassigned case responsibility", "clear": "Cleared case responsibility"}


class Unavailable(ValueError):
    """State or authority cannot be read safely; never treated as unassigned."""


class Conflict(ValueError):
    """Another operation changed the assignment or reused this retry handle."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _email(value):
    if not isinstance(value, str) or len(value) > 254:
        raise ValueError("Choose a staff account.")
    value = value.strip().lower()
    if not value or "@" not in value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("Choose a staff account.")
    return value


def _safe(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise Unavailable("Assignment paths cannot use links or reparse points.")
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise Unavailable("Assignment path is unavailable.") from exc
        if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise Unavailable("Assignment paths cannot use links or reparse points.")


def _json(path, missing=None):
    _safe(path)
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return missing
    except OSError as exc:
        raise Unavailable("Assignment or lifecycle record is unavailable.") from exc
    if len(raw) > MAX_BYTES:
        raise Unavailable("Assignment or lifecycle record is too large.")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise Unavailable("Assignment or lifecycle record is damaged.") from exc
    if not isinstance(value, dict):
        raise Unavailable("Assignment or lifecycle record has an invalid shape.")
    return value


def _person(value):
    if not isinstance(value, dict) or set(value) != {"email", "name", "role"}:
        raise Unavailable("Assignment identity is damaged.")
    try:
        if _email(value["email"]) != value["email"]:
            raise ValueError()
    except ValueError as exc:
        raise Unavailable("Assignment identity is damaged.") from exc
    if value["role"] not in ROLES or not isinstance(value["name"], str) or not value["name"].strip() or len(value["name"]) > 200:
        raise Unavailable("Assignment identity is damaged.")
    return value


def _request(actor, action, target, reason, revision):
    return {"actor": actor, "action": action, "target": target, "reason": reason, "expected_revision": revision}


def _validate(data):
    if set(data) != {"version", "revision", "assignee", "history"} or type(data["version"]) is not int or data["version"] != VERSION or type(data["revision"]) is not int:
        raise Unavailable("Assignment state is damaged.")
    history = data["history"]
    if not isinstance(history, list) or len(history) > MAX_HISTORY or data["revision"] != len(history):
        raise Unavailable("Assignment history is damaged.")
    previous, seen = None, set()
    for revision, row in enumerate(history, 1):
        if not isinstance(row, dict) or set(row) != {"revision", "operation", "payload_sha256", "actor", "action", "before", "after", "reason", "at", "audit"}:
            raise Unavailable("Assignment history is damaged.")
        actor = _person(row["actor"])
        target = _person(row["after"]) if row["after"] is not None else None
        if (type(row["revision"]) is not int or row["revision"] != revision or row["before"] != previous
                or not isinstance(row["operation"], str) or not OPERATION.fullmatch(row["operation"]) or row["operation"] in seen
                or row["action"] not in WORDS or not isinstance(row["reason"], str) or len(row["reason"]) > 1000
                or not isinstance(row["at"], str) or clock.parse(row["at"]) is None):
            raise Unavailable("Assignment history is damaged.")
        if (row["action"] == "claim" and (previous is not None or target is None or target["email"] != actor["email"] or row["reason"]) or
                (row["action"] == "clear") != (target is None)):
            raise Unavailable("Assignment history is damaged.")
        request = _request(actor["email"], row["action"], target["email"] if target else None, row["reason"], revision - 1)
        if row["payload_sha256"] != _digest(request):
            raise Unavailable("Assignment retry payload is damaged.")
        audit = row["audit"]
        if not isinstance(audit, dict) or audit.get("state") not in ("pending", "recorded"):
            raise Unavailable("Assignment audit reference is damaged.")
        if audit["state"] == "recorded" and (set(audit) != {"state", "hash", "at"} or not isinstance(audit["hash"], str)
                or not HASH.fullmatch(audit["hash"]) or not isinstance(audit["at"], str) or clock.parse(audit["at"]) is None):
            raise Unavailable("Assignment audit reference is damaged.")
        if audit["state"] == "pending" and set(audit) != {"state"}:
            raise Unavailable("Assignment audit reference is damaged.")
        seen.add(row["operation"])
        previous = target
    if data["assignee"] != previous:
        raise Unavailable("Assignment state disagrees with its history.")
    return data


def summary(client_dir):
    """Derived roster field, read once while rebuilding a known case entry.

    Current accounts/access, not this snapshot, determine effective ownership.
    No history or operation retry handle belongs in a paged roster response.
    """
    try:
        data = _validate(_json(Path(client_dir) / FILE, {"version": VERSION, "revision": 0, "assignee": None, "history": []}))
        return {"state": "assigned" if data["assignee"] else "unassigned", "revision": data["revision"], "assignee": data["assignee"],
                "audit_pending": any(r["audit"]["state"] != "recorded" for r in data["history"])}
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return {"state": "unavailable", "revision": None, "assignee": None, "audit_pending": True}


class Assignments:
    """Internal API. accounts() supplies fresh Accounts.users(); ACL uses current files.

    The returned email identities belong to internal staff data. Routes must map
    them to the existing public person IDs rather than expose account emails.
    """
    def __init__(self, clients_root, jobs_root, accounts: Callable, can_access: Callable = restricted.visible_to):
        self.clients_root = Path(clients_root).absolute()
        self.jobs_root = Path(jobs_root).absolute()
        self.accounts = accounts
        self.can_access = can_access

    def _folder(self, case):
        if not isinstance(case, str) or not case or case in (".", "..") or len(case) > 200 or any(c in case for c in ("/", "\\", "\0")):
            raise LookupError("Unknown case.")
        folder = self.clients_root / case
        _safe(folder)
        try:
            known = folder.is_dir() and case in os.listdir(self.clients_root)
        except OSError as exc:
            raise Unavailable("Case directory is unavailable.") from exc
        if not known:
            raise LookupError("Unknown case.")
        return folder

    def read(self, case):
        """Internal read; routes must authorize before returning any case information."""
        try:
            return _validate(_json(self._folder(case) / FILE, {"version": VERSION, "revision": 0, "assignee": None, "history": []}))
        except Unavailable:
            raise
        except (TypeError, KeyError, AttributeError, ValueError) as exc:
            raise Unavailable("Assignment state is damaged.") from exc

    def _accounts(self):
        try:
            rows = self.accounts()
            if not isinstance(rows, (list, tuple)):
                raise ValueError()
            found = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError()
                email = _email(row.get("email"))
                if email in found or type(row.get("active")) is not bool:
                    raise ValueError()
                found[email] = row
            return found
        except Exception as exc:
            raise Unavailable("Current staff accounts are unavailable.") from exc

    def _eligible(self, email, accounts, folder):
        user = accounts.get(email)
        if not user or user.get("active") is not True or user.get("role") not in ROLES:
            raise PermissionError("Choose an active attorney or paralegal account.")
        try:
            allowed = self.can_access(user, folder)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise Unavailable("Current case access is unavailable.") from exc
        if allowed is not True:
            raise PermissionError("This account cannot access the case.")
        person = {"email": email, "name": user.get("name"), "role": user["role"]}
        return _person(person)

    def view(self, case, actor_email):
        folder = self._folder(case)
        accounts = self._accounts()
        self._eligible(_email(actor_email), accounts, folder)
        data = self.read(case)
        state = "unassigned" if data["assignee"] is None else "assigned"
        if data["assignee"]:
            try:
                self._eligible(data["assignee"]["email"], accounts, folder)
            except PermissionError:
                state = "needs_attention"
        return {"state": state, **data}

    def _open(self, folder):
        import engagement
        import conflicts
        rec = _json(folder / engagement.FILE, {})
        end = rec.get("end")
        if end is not None and (not isinstance(end, dict) or end.get("state") not in engagement.ENDED):
            raise Unavailable("Case end state is unavailable.")
        if end or rec.get("destroyed"):
            raise Conflict("The case has ended; assignment history is retained.")
        check = _json(folder / conflicts.FILE, {})
        decision = check.get("decision")
        if decision is not None and (not isinstance(decision, dict) or decision.get("decision") not in conflicts.DECISIONS):
            raise Unavailable("Case conflict state is unavailable.")
        if (decision or {}).get("decision") == "declined" or check.get("abandoned"):
            raise Conflict("The case was declined or abandoned; assignment history is retained.")
        import purge
        purges = _json(folder.parent.parent / purge.PURGES_FILE, {"cases": {}}).get("cases")
        destroyed = _json(folder.parent.parent / engagement.DESTROYED_FILE, {"cases": []}).get("cases")
        if (not isinstance(purges, dict) or (folder.name in purges and not isinstance(purges[folder.name], dict))
                or not isinstance(destroyed, list) or any(not isinstance(r, dict) for r in destroyed)):
            raise Unavailable("Case destruction state is unavailable.")
        # Reuse Q1's existing wait/destruction guard; do not invent a second lifecycle.
        from client_file import allowed
        try:
            allowed(folder)
        except ValueError as exc:
            raise Conflict(str(exc)) from exc

    def _write(self, folder, data):
        _validate(data)
        raw = _canonical(data)
        if len(raw) > MAX_BYTES:
            raise Unavailable("Assignment history is full; no retry proof was discarded.")
        target, part = folder / FILE, folder / PART
        _safe(target); _safe(part)
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600) if hasattr(os, "fchmod") else part.chmod(0o600)
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(part, target)
        if os.name == "posix":
            fd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _audit(self, folder, data):
        """Reconcile the existing ledger, not a second chain. References are not seals."""
        for change in data["history"]:
            if change["audit"]["state"] == "recorded":
                continue
            try:
                matching = [r for r in events.rows(events.base_path(folder.parent.parent), case=folder.name)
                            if r.get("kind") == KIND and r.get("version") == change["revision"]]
            except OSError:
                break  # a committed operation remains visibly pending if the ledger cannot be read
            commitment = _digest({k: v for k, v in change.items() if k != "audit"})
            expected = {"who": change["actor"]["name"], "role": change["actor"]["role"], "via": "staff",
                        "action": change["action"], "what": f"{WORDS[change['action']]} (assignment SHA-256 {commitment})"}
            if matching:
                if len(matching) != 1 or any(matching[0].get(k) != v for k, v in expected.items()) or events.row_hash(matching[0]) != matching[0].get("hash"):
                    raise Unavailable("Assignment ledger reconciliation is unavailable.")
                row = matching[0]
            else:
                row = events.record("assignment", change["action"], expected["what"], case_dir=folder,
                                    who=expected["who"], role=expected["role"], via="staff", version=change["revision"])
            if row is None:
                break  # committed durable history stays visibly ledger-pending
            change["audit"] = {"state": "recorded", "hash": row["hash"], "at": row["at"]}
            self._write(folder, data)  # append-before-marker crash retries find the existing row

    @staticmethod
    def _outcome(data, change):
        return {"committed": True, "committed_revision": change["revision"], "revision": data["revision"],
                "assignee": data["assignee"], "operation_assignee": change["after"],
                "audit_pending": any(r["audit"]["state"] != "recorded" for r in data["history"])}

    def change(self, case, actor_email, action, *, expected_revision, operation_id, target_email=None, reason=""):
        """Authenticated actor identity supplied by route; role/name are loaded fresh.

        An identical retry returns its original committed revision plus current
        state. It cannot restore a former assignee over a later reassignment.
        """
        actor_email = _email(actor_email)
        if not isinstance(action, str) or action not in WORDS or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Choose an assignment action and current revision.")
        if not isinstance(operation_id, str) or not OPERATION.fullmatch(operation_id):
            raise ValueError("Use a new assignment operation ID.")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError("Use a reason of at most 1,000 characters.")
        reason = " ".join(reason.split())
        if action == "claim":
            if target_email is not None and _email(target_email) != actor_email or reason:
                raise ValueError("A self-claim names only the signed-in staff member.")
            target = actor_email
        elif action == "reassign":
            target = _email(target_email)
        else:
            if target_email is not None:
                raise ValueError("Clearing assignment does not select an assignee.")
            target = None
        request = _request(actor_email, action, target, reason, expected_revision)
        folder = self._folder(case)
        _safe(self.jobs_root / "locks")
        _safe(self.jobs_root / "locks" / (hashlib.sha256(case.encode("utf-8")).hexdigest()[:24] + ".lock"))
        with jobs.case_lock(self.jobs_root, case, timeout=jobs.BUSY_WAIT):
            folder = self._folder(case)
            accounts = self._accounts()
            actor = self._eligible(actor_email, accounts, folder)
            data = self.read(case)
            existing = next((r for r in data["history"] if r["operation"] == operation_id), None)
            if existing:
                if existing["payload_sha256"] != _digest(request):
                    raise Conflict("The assignment operation ID belongs to another request.")
                try:
                    self._open(folder)
                except Conflict:
                    return self._outcome(data, existing)  # no writes on ended/destruction-wait cases
                self._audit(folder, data)
                return self._outcome(data, existing)
            self._open(folder)
            if data["revision"] != expected_revision or action == "claim" and data["assignee"] is not None:
                raise Conflict("The assignment changed; review the current case owner.")
            if len(data["history"]) >= MAX_HISTORY:
                raise Unavailable("Assignment history is full; no retry proof was discarded.")
            assignee = self._eligible(target, accounts, folder) if target else None
            change = {"revision": data["revision"] + 1, "operation": operation_id, "payload_sha256": _digest(request), "actor": actor,
                      "action": action, "before": data["assignee"], "after": assignee, "reason": reason, "at": clock.stamp(), "audit": {"state": "pending"}}
            data["history"].append(change)
            data.update(revision=change["revision"], assignee=assignee)
            self._write(folder, data)
            self._audit(folder, data)
            return self._outcome(data, change)
