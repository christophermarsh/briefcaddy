"""Case-owned complete portal originals, with an explicit multiwrite hold.

No provider, profile/consent mutation, enrollment or automatic review approval.
Case -> portal upload lock order; the installed worker retains its queue locks.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

import clock
import jobs
import oslock
from law_app.adapters.persistence.filesystem.portal import _atomic, _safe

FILE = "source-association.json"
HASH = re.compile(r"[0-9a-f]{64}")


def _read(case):
    path = _safe(Path(case) / FILE)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("version") != 1
                or value.get("client") != Path(case).name
                or value.get("status") not in {"pending", "ready"}
                or not isinstance(value.get("actor"), str)
                or not isinstance(value.get("destination"), str)
                or not isinstance(value.get("initial"), bool)
                or not isinstance(value.get("files"), list)):
            raise ValueError()
        if value["destination"] != str(Path(case).absolute().parent.parent.parent / "clients" / Path(case).name / "source"):
            raise ValueError()
        history = value.get("authorizations")
        if history is not None and (not isinstance(history, list) or any(not isinstance(row, dict) or not isinstance(row.get("actor"), str) or not isinstance(row.get("at"), str) for row in history)):
            raise ValueError()
        names = set()
        for item in value["files"]:
            if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                    or Path(item["name"]).name != item["name"] or any(c in item["name"] for c in "/\\") or not item["name"].endswith(".pdf")
                    or item["name"] in names or not isinstance(item.get("sha256"), str)
                    or not HASH.fullmatch(item["sha256"]) or item.get("origin") not in {"portal", "folder", "scan_inbox", "drive", "m365", "filevine", "docketwise", "clio"}
                    or not isinstance(item.get("upload"), str)):
                raise ValueError()
            names.add(item["name"])
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("Source association is damaged; recovery is required.") from None


def hold(case):
    """Pure read for evidence gates; absence preserves legacy behavior."""
    try:
        value = _read(case)
        if value is None:
            return False
        if value["status"] != "ready":
            return True
        meta = json.loads(_safe(Path(case) / "meta.json").read_text(encoding="utf-8"))
        return not isinstance(meta, dict) or meta.get("source_folder") != value["destination"]
    except (ValueError, OSError):
        return True


def assert_ready(case):
    if hold(case):
        raise ValueError("Source association is incomplete; recover it before processing.")


def _scope(root, store, client):
    from connectors.drive_intake import FirmScope
    from review.staff_upload_recovery import _scope as staff_scope
    scope = FirmScope(root)
    staff_scope(scope.cases, store, client, scope.queue, scope.documents)
    import schema_path
    if schema_path.ROOT.absolute() != schema_path.schemas_in(scope.root):
        raise ValueError("The effective schemas do not belong to this installation.")
    for name, expected in {"I485_EVENTS": scope.data / "events.jsonl", "I485_INDEX": scope.data / "index.db",
                           "I485_QUERY_DB": scope.data / "query.db", "I485_CASES": scope.cases,
                           "I485_READER_EXAMPLES": scope.data / "reader_examples",
                           "PORTAL_DATA": scope.portal, "I485_CLIENTS_ROOT": scope.documents}.items():
        if os.environ.get(name) and Path(os.environ[name]).absolute() != expected:
            raise ValueError("The worker inherits another installation's source stores.")
    return scope, _safe(scope.cases / client), _safe(store.client_dir(client) / "uploads"), _safe(scope.documents / client / "source")


def _authority(scope, case, store, email, actor_reader, may_access):
    from review.staff_upload_recovery import _authority as staff_authority, _actor_reader
    import restricted
    return staff_authority(scope.cases, case, store, email, actor_reader or _actor_reader(scope.cases), may_access or restricted.visible_to)


def _hash(path):
    path = _safe(path)
    if not path.is_file():
        raise ValueError("A retained original is unavailable.")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(store, client, portal):
    rows = store.uploads(client)
    by_file = {}
    for row in rows:
        name = row.get("stored")
        if (not isinstance(name, str) or Path(name).name != name or any(c in name for c in "/\\") or not name.endswith(".pdf")
                or name in by_file or not isinstance(row.get("id"), str)):
            raise ValueError("Portal original identity is unavailable or ambiguous.")
        by_file[name] = row
    names = set()
    if portal.exists():
        for path in portal.iterdir():
            _safe(path)
            if path.is_file() and path.suffix == ".pdf":
                names.add(path.name)
    if names != set(by_file):
        raise ValueError("The complete portal original set does not match its upload identities.")
    out = [{"name": name, "sha256": _hash(portal / name), "upload": by_file[name]["id"],
            "origin": by_file[name].get("source") or "portal"} for name in sorted(names)]
    import documents
    if any(item["origin"] not in documents.SOURCES for item in out):
        raise ValueError("A portal original has an unknown origin.")
    return out


def _validate(value, case, destination):
    if value["destination"] != str(destination) or value["client"] != case.name:
        raise ValueError("Source association leaves this case's canonical folder.")


def _origins(case, value, destination):
    import documents
    origins = {item["name"]: item["origin"] for item in value["files"]}
    for record in (documents.read(case) or {}).get("documents", []):
        for name in record.get("files", []):
            origin = record.get("source")
            if origin not in documents.SOURCES or (name in origins and origins[name] != origin):
                raise ValueError("An original's actual origin is ambiguous.")
            origins[name] = origin
    for path in destination.iterdir() if destination.exists() else ():
        _safe(path)
        if path.suffix == ".pdf" and path.name not in origins:
            raise ValueError("An unprocessed canonical original has no verified origin; finish its intake first.")
    return origins


def verify(case, destination):
    """Drive validates the ready original association without changing it."""
    value = _read(case)
    if value is None:
        return None
    assert_ready(case)
    destination = _safe(destination)
    _validate(value, Path(case), destination)
    meta_path = _safe(Path(case) / "meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or meta.get("source_folder") != str(destination):
        raise ValueError("The canonical association metadata is not current.")
    for item in value["files"]:
        if _hash(destination / item["name"]) != item["sha256"]:
            raise ValueError("An associated original changed; source recovery is required.")
    return value


def _publish(scope, case, store, portal, destination, value, email, actor_reader=None, may_access=None):
    _validate(value, case, destination)
    meta_path = _safe(case / "meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or meta.get("source_folder") not in {str(portal), str(destination)}:
        raise ValueError("The existing source association changed.")
    if _snapshot(store, case.name, portal) != value["files"]:
        raise ValueError("Portal originals changed during association; recovery requires review.")
    planned = {item["name"] for item in value["files"]}
    if destination.exists():
        for path in destination.iterdir():
            _safe(path)
            if value.get("initial") and path.suffix == ".pdf" and path.name not in planned:
                raise ValueError("The initial canonical source already contains unrelated originals.")
    _authority(scope, case, store, email, actor_reader, may_access)
    destination.mkdir(parents=True, exist_ok=True)
    for item in value["files"]:
        _authority(scope, case, store, email, actor_reader, may_access)
        target = _safe(destination / item["name"])
        if target.exists():
            if _hash(target) != item["sha256"]:
                raise ValueError("A source filename collision has different bytes; nothing is overwritten.")
            continue
        data = _safe(portal / item["name"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("A portal original changed during copying.")
        destination.mkdir(parents=True, exist_ok=True)
        part = _safe(target.with_name(target.name + ".association.part"))
        fd = os.open(part, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        _authority(scope, case, store, email, actor_reader, may_access)
        os.replace(part, target)
    _authority(scope, case, store, email, actor_reader, may_access)
    if _snapshot(store, case.name, portal) != value["files"]:
        raise ValueError("Portal originals changed before publication.")
    for item in value["files"]:
        if _hash(destination / item["name"]) != item["sha256"]:
            raise ValueError("The complete canonical originals could not be verified.")
    meta_path = _safe(case / "meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or meta.get("source_folder") not in {str(portal), str(destination)}:
        raise ValueError("The existing source association changed.")
    _atomic(meta_path, {**meta, "source_folder": str(destination)})
    _authority(scope, case, store, email, actor_reader, may_access)
    _atomic(case / FILE, {**value, "status": "ready"})
    return destination


def associate(root, store, client, *, actor_email, actor_reader=None, may_access=None, use_policies=True):
    """Explicit protected operation; existing main profile only, no enrollment."""
    from portal.communication_consent import data_gate
    scope, case, portal, destination = _scope(root, store, client)
    with data_gate(scope.data), jobs.case_lock(scope.queue, client), store._lock, oslock.locked(_safe(portal.parent / "upload.lock"), timeout=10):
        _authority(scope, case, store, actor_email, actor_reader, may_access)
        value = _read(case)
        if value is not None:
            _validate(value, case, destination)
            if value["status"] == "ready":
                verify(case, destination)
                if value["actor"] == actor_email:
                    return {"source_folder": str(destination), "associated": True, "review_required": True}
                _origins(case, value, destination)
                value = {**value, "initial": False}
            if value["actor"] != actor_email:
                # Explicit named handover: live authority above is required.
                # History is attribution only; workers reread the current account.
                value = {**value, "actor": actor_email, "status": "pending",
                         "authorizations": list(value.get("authorizations") or []) + [{"actor": actor_email, "at": clock.stamp()}]}
                _atomic(case / FILE, value)
        else:
            from portal.engine import process_client
            if not (case / "fact_graph.json").exists():
                process_client(store, client, out_root=scope.cases, use_policies=use_policies)
            meta = json.loads(_safe(case / "meta.json").read_text(encoding="utf-8"))
            if meta.get("source_folder") != str(portal):
                raise ValueError("Only this case's complete portal source can be associated.")
            value = {"version": 1, "client": client, "actor": actor_email, "destination": str(destination),
                     "files": _snapshot(store, client, portal), "status": "pending", "initial": True,
                     "authorizations": [{"actor": actor_email, "at": clock.stamp()}]}
            _authority(scope, case, store, actor_email, actor_reader, may_access)
            _atomic(case / FILE, value)
        _publish(scope, case, store, portal, destination, value, actor_email, actor_reader, may_access)
        return {"source_folder": str(destination), "associated": True, "review_required": True}


def engine_sources(root, store, client):
    """Reconcile later portal generations, retaining nonportal originals/origins."""
    scope, case, portal, destination = _scope(root, store, client)
    value = verify(case, destination)
    if value is None:
        return None
    with store._lock, oslock.locked(_safe(portal.parent / "upload.lock"), timeout=10):
        _authority(scope, case, store, value["actor"], None, None)
        incoming = _snapshot(store, client, portal)
        old = {item["name"]: item for item in value["files"]}
        fresh = {item["name"]: item for item in incoming}
        if any(fresh.get(name) != item for name, item in old.items()):
            raise ValueError("Previously associated portal originals changed or disappeared.")
        if incoming != value["files"]:
            value = {**value, "files": incoming, "status": "pending", "initial": False}
            _atomic(case / FILE, value)
            _publish(scope, case, store, portal, destination, value, value["actor"])
        origins = _origins(case, {**value, "files": incoming}, destination)
        uploads = store.uploads(client)
        actual = {up["stored"]: up for up in uploads}
        rows = []
        for path in sorted(destination.iterdir()):
            _safe(path)
            if path.suffix != ".pdf":
                continue
            if path.name not in origins:
                raise ValueError("An unprocessed canonical original has no verified origin; finish its intake first.")
            rows.append(actual.get(path.name) or {"stored": path.name, "source": origins[path.name]})
        return destination, uploads, rows


def purge_pending(clients, client, documents_root):
    """Q1 case lock held: remove only a proven own canonical copy generation.

    Needed when pending publication still leaves meta pointing to portal uploads.
    Canonical ready copies use the same ownership proof; portal/case cleanup is Q1.
    """
    case = _safe(Path(clients) / client)
    value = _read(case)
    if value is None:
        return 0
    documents_root = _safe(documents_root)
    destination = _safe(documents_root / client / "source")
    _validate(value, case, destination)
    for other in Path(clients).iterdir():
        if not other.is_dir() or other.name == client:
            continue
        meta_path = _safe(other / "meta.json")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            named = meta.get("source_folder") if isinstance(meta, dict) else None
            if named and _safe(Path(named)).is_relative_to(destination.parent):
                raise ValueError("Another case references this canonical generation; cleanup is unresolved.")
    from purge import _rmtree
    return _rmtree(destination.parent)
