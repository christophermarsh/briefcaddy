"""Case-scoped staff upload storage/job retry identity, not exactly-once reading.

Trusted caller supplies roots and live account/ACL readers; none come from the
HTTP body. Lock order is case -> portal upload -> queue submit. Existing reader
owns evidence/audit. This module adds no custom event chain or provider effect.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re

import clock
import jobs
import oslock
import restricted
from portal.queue_bridge import _safe, _atomic
from portal.store import CLIENT_ID

TOKEN = re.compile(r"[0-9a-f]{32,64}")
HASH = re.compile(r"[0-9a-f]{64}")


def key(attempt):
    if not isinstance(attempt, str) or not TOKEN.fullmatch(attempt):
        raise ValueError("invalid_staff_upload_attempt")
    return hashlib.sha256(attempt.encode()).hexdigest()


def _read(path):
    path = _safe(path)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("staff_upload_receipt_damaged") from None
    if not isinstance(value, dict):
        raise ValueError("staff_upload_receipt_damaged")
    return value


def _actor_reader(clients):
    from review.auth import Accounts
    path = _safe(clients.parent / "review_users.json")
    return lambda email: next((u for u in Accounts(path).users() if u["email"] == email), None)


def _scope(clients_root, store, client, queue, documents):
    import prospects
    if not isinstance(client, str) or not CLIENT_ID.fullmatch(client) or client.startswith(prospects.PREFIX):
        raise ValueError("Choose a main-store client.")
    clients, queue, documents = _safe(Path(clients_root)), _safe(Path(queue)), _safe(Path(documents))
    if not queue.is_relative_to(clients.parent) or _safe(jobs.folder_for(clients)) != queue:
        raise ValueError("The upload queue does not match this installation's configured queue.")
    case = _safe(clients / client)
    if not case.is_dir():
        raise ValueError("Add this client through the protected front desk first.")
    if store is not None and _safe(store.root) != clients.parent / "portal":
        raise ValueError("The upload store does not belong to this main installation.")
    if store is not None:
        _safe(store.client_dir(client) / "profile.json")
        _safe(store.client_dir(client) / "uploads.json")
    return clients, case, queue, documents


def _authority(clients, case, store, email, actor_reader, may_access):
    if not case.is_dir():
        raise ValueError("This case is no longer available.")
    actor = actor_reader(email)
    if not isinstance(actor, dict) or actor.get("email") != email or not actor.get("active") or actor.get("role") not in {"attorney", "paralegal"}:
        raise PermissionError("An active staff account is required.")
    if not may_access(actor, case):
        raise PermissionError("This case is unavailable to this account.")
    from case_assignment import Assignments
    Assignments(clients, jobs.folder_for(clients), lambda: [])._open(case)
    if store is not None and (store.profile(case.name).get("declined_on") or (store.profile(case.name).get("status") == "declined")):
        raise ValueError("This client was declined; no upload is accepted.")
    return actor


def _folder(clients, case, store, documents):
    processed = _safe(case / "fact_graph.json").is_file()
    portal = _safe(clients.parent / "portal" / "clients" / case.name / "uploads")
    if not processed:
        if store is None:
            raise ValueError("This client needs its existing portal profile.")
        store.profile(case.name)
        return portal, False
    meta = _read(case / "meta.json")
    named = meta.get("source_folder") if meta else None
    if not isinstance(named, str) or not named:
        raise ValueError("The case's document folder is unavailable.")
    folder = _safe(Path(named))
    own_source = _safe(documents / case.name / "source")
    if not folder.is_dir() or folder not in (own_source, portal):
        raise ValueError("The case's source folder is not its own canonical or portal source.")
    if folder == portal and store is None:
        raise ValueError("The current portal store is required for this source.")
    return folder, True


def _validate(rec, attempt_key, case):
    if (rec.get("version") != 1 or rec.get("key") != attempt_key or rec.get("client") != case.name
            or rec.get("status") not in {"pending", "stored", "reserved"}
            or not isinstance(rec.get("actor"), str) or not isinstance(rec.get("original"), str)
            or not isinstance(rec.get("folder"), str) or not isinstance(rec.get("staged"), dict)
            or not all(isinstance(rec.get(k), str) and HASH.fullmatch(rec[k]) for k in ("source_sha256", "sha256"))):
        raise ValueError("staff_upload_receipt_damaged")
    stage = rec["staged"]
    if (stage.get("staff_receipt") != attempt_key or not isinstance(stage.get("name"), str)
            or Path(stage["name"]).name != stage["name"] or not stage["name"].endswith(".pdf")
            or not isinstance(stage.get("pages"), int) or stage["pages"] < 1
            or not isinstance(stage.get("by"), str) or not stage["by"].strip()
            or not isinstance(stage.get("case"), bool)):
        raise ValueError("staff_upload_receipt_damaged")
    row = rec.get("portal_record")
    if row is not None and (not isinstance(row, dict) or row.get("stored") != stage["name"] or row.get("sha256") != rec["sha256"] or row.get("source_sha256") != rec["source_sha256"] or row.get("source") != "folder"):
        raise ValueError("staff_upload_receipt_damaged")
    return rec


def _proof(queue, client, attempt_key):
    scope = hashlib.sha256(json.dumps(["staff_upload", client, attempt_key], separators=(",", ":")).encode()).hexdigest()
    return queue / ("operation-" + scope + ".json")


def _observation(rec, queue):
    target = _safe(Path(rec["folder"]) / rec["staged"]["name"])
    received = target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == rec["sha256"]
    proof = _read(_proof(queue, rec["client"], rec["key"]))
    job = None
    if proof is not None:
        payload = hashlib.sha256(json.dumps(rec["staged"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (proof.get("kind") != "staff_upload" or proof.get("client") != rec["client"] or proof.get("payload") != payload
                or not isinstance(proof.get("id"), str) or not jobs.NAME.fullmatch(proof["id"] + ".json") or proof.get("state") != "reserved"):
            raise ValueError("staff_upload_operation_damaged")
        job = jobs.get(queue, proof["id"])
        if job is not None and (job.get("id") != proof["id"] or job.get("kind") != "staff_upload" or job.get("client") != rec["client"] or job.get("args") != rec["staged"]):
            raise ValueError("staff_upload_job_payload_changed")
    return {"name": rec["staged"]["name"], "pages": rec["staged"]["pages"], "by": rec["staged"]["by"],
            "received": received, "status": "unavailable" if (proof and job is None) or (rec["status"] == "reserved" and proof is None) or (not received and rec["status"] != "pending") else rec["status"],
            "reading": bool(job and job.get("state") in {"queued", "running"}),
            "processed": bool(received and job and job.get("state") == "done" and (job.get("result") or {}).get("processed") is True),
            "review_required": True, "job": jobs.view(job) if job else None}


def accept(clients_root, store, client_id, original, data, attempt, *, actor_email, jobs_root, documents_root,
           wake_worker, actor_reader=None, may_access=restricted.visible_to, case_timeout=None):
    from review.front_desk import _prepare_scan, _scan_name
    from overnight import source_signature
    if not isinstance(original, str) or len(original) > 255 or not isinstance(data, bytes):
        raise ValueError("Invalid staff upload name or bytes.")
    attempt_key = key(attempt)
    clients, case, queue, documents = _scope(clients_root, store, client_id, jobs_root, documents_root)
    actor_reader = actor_reader or _actor_reader(clients)
    with jobs.case_lock(queue, client_id, case_timeout), ExitStack() as locks:
        actor = _authority(clients, case, store, actor_email, actor_reader, may_access)
        folder, processed = _folder(clients, case, store, documents)
        portal_folder = clients.parent / "portal" / "clients" / client_id / "uploads"
        if folder == portal_folder:
            locks.enter_context(store._lock)
            locks.enter_context(oslock.locked(_safe(folder.parent / "upload.lock"), timeout=10))
        receipt_path = _safe(case / "staff-upload-receipts" / (attempt_key + ".json"))
        rec = _read(receipt_path)
        source_hash = hashlib.sha256(data).hexdigest()
        if rec is not None:
            _validate(rec, attempt_key, case)
            if rec["actor"] != actor_email or rec["original"] != original or rec["source_sha256"] != source_hash:
                raise ValueError("staff_upload_attempt_conflict")
            if Path(rec["folder"]) != folder:
                raise ValueError("The case's source association changed; review the existing upload.")
        elif _proof(queue, client_id, attempt_key).exists():
            raise ValueError("staff_upload_receipt_unavailable")
        if rec is None:
            converted, pages = _prepare_scan(data)
            stem = Path(_scan_name(clock.today().isoformat(), original)).stem
            name = stem + "-" + attempt_key[:32] + ".pdf"
            target = _safe(folder / name)
            if target.exists():
                raise ValueError("staff_upload_target_collision")
            staged = {"name": name, "pages": pages, "by": str(actor.get("name") or actor_email), "case": processed, "staff_receipt": attempt_key}
            if processed:
                meta = case / "meta.json"
                staged.update(before=source_signature(folder), meta_mtime=meta.stat().st_mtime,
                              newest=max((p.stat().st_mtime for p in folder.glob("*.pdf")), default=0.0))
            row = None
            if folder == portal_folder:
                row = {"id": Path(name).stem, "doc_id": "folder", "filename": original[:120], "stored": name, "size": len(converted),
                       "sha256": hashlib.sha256(converted).hexdigest(), "source_sha256": source_hash, "uploaded_at": clock.stamp(),
                       "status": "checked", "source": "folder", "by": staged["by"]}
            rec = {"version": 1, "key": attempt_key, "client": client_id, "actor": actor_email, "original": original,
                   "source_sha256": source_hash, "sha256": hashlib.sha256(converted).hexdigest(), "folder": str(folder),
                   "staged": staged, "portal_record": row, "status": "pending"}
            _atomic(receipt_path, rec)
        target = _safe(folder / rec["staged"]["name"])
        if target.exists():
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != rec["sha256"]:
                raise ValueError("staff_upload_retained_source_changed")
        else:
            if rec["status"] != "pending":
                raise ValueError("staff_upload_retained_source_missing")
            converted, _ = _prepare_scan(data)
            if hashlib.sha256(converted).hexdigest() != rec["sha256"]:
                raise ValueError("staff_upload_conversion_changed")
            folder.mkdir(parents=True, exist_ok=True)
            part = _safe(target.with_suffix(".part"))
            fd = os.open(part, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(converted)
                out.flush()
                os.fsync(out.fileno())
            os.replace(part, target)
        if rec["portal_record"] is not None:
            uploads = store.uploads(client_id)
            row = rec["portal_record"]
            matches = [u for u in uploads if u.get("id") == row["id"]]
            if len(matches) > 1 or (matches and any(matches[0].get(k) != row[k] for k in ("stored", "sha256", "source_sha256", "source", "doc_id"))):
                raise ValueError("staff_upload_portal_record_changed")
            if not matches:
                store.update_uploads(client_id, uploads + [row])
        if rec["status"] != "reserved":
            rec["status"] = "stored"
            _atomic(receipt_path, rec)
        _authority(clients, case, store, actor_email, actor_reader, may_access)
        proof = _read(_proof(queue, client_id, attempt_key))
        if proof is not None and (proof.get("kind") != "staff_upload" or proof.get("client") != client_id or not isinstance(proof.get("id"), str) or not isinstance(proof.get("payload"), str)):
            raise ValueError("staff_upload_operation_damaged")
        if rec["status"] == "reserved" and proof is None:
            result = _observation(rec, queue)
        else:
            job = jobs.submit(queue, "staff_upload", client_id, by=rec["staged"]["by"], args=rec["staged"], operation_id=attempt_key)
            rec["status"] = "reserved"
            _atomic(receipt_path, rec)
            result = _observation(rec, queue)
            if job.get("state") == "unavailable":
                result["status"] = "unavailable"
    result["worker_available"] = bool(wake_worker(clients, store.root if store is not None else None, queue))
    return result


def outcome(clients_root, store, client_id, attempt, *, actor_email, jobs_root, documents_root,
            actor_reader=None, may_access=restricted.visible_to, case_timeout=None):
    attempt_key = key(attempt)
    clients, case, queue, documents = _scope(clients_root, store, client_id, jobs_root, documents_root)
    with jobs.case_lock(queue, client_id, case_timeout):
        _authority(clients, case, store, actor_email, actor_reader or _actor_reader(clients), may_access)
        rec = _read(case / "staff-upload-receipts" / (attempt_key + ".json"))
        if rec is None:
            return {"status": "unknown", "received": False, "processed": False}
        _validate(rec, attempt_key, case)
        if rec["actor"] != actor_email:
            raise PermissionError("This upload attempt belongs to another account.")
        folder, _ = _folder(clients, case, store, documents)
        if Path(rec["folder"]) != folder:
            raise ValueError("The case's source association changed; review the existing upload.")
        return _observation(rec, queue)


def validate_for_read(clients_root, store, client_id, staged, by):
    """Worker authorization comes from current accounts/config, never job role."""
    from purge import _documents_root
    clients = _safe(Path(clients_root))
    queue = jobs.folder_for(clients)
    clients, case, queue, documents = _scope(clients, store, client_id, queue, _documents_root(clients.parent))
    attempt_key = staged.get("staff_receipt")
    if not isinstance(attempt_key, str) or not HASH.fullmatch(attempt_key):
        raise ValueError("staff_upload_receipt_damaged")
    rec = _read(case / "staff-upload-receipts" / (attempt_key + ".json"))
    if rec is None:
        raise ValueError("staff_upload_receipt_unavailable")
    _validate(rec, attempt_key, case)
    with jobs.case_lock(queue, client_id):
        _authority(clients, case, store, rec["actor"], _actor_reader(clients), restricted.visible_to)
        folder, _ = _folder(clients, case, store, documents)
        if staged != rec["staged"] or by != staged["by"] or Path(rec["folder"]) != folder:
            raise ValueError("staff_upload_job_payload_changed")
        if _observation(rec, queue)["job"] is None:
            raise ValueError("staff_upload_operation_unavailable")
        target = _safe(folder / staged["name"])
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != rec["sha256"]:
            raise ValueError("staff_upload_retained_source_changed")


def purge_job_staging(queue, cases):
    """Q1 under case lock; exact staff job/proof temp identities only.

    Return removed/unresolved counts, never client/file values. Locks remain.
    Canonical job/proof deletion belongs to the existing Q1 loop afterward.
    """
    queue = _safe(Path(queue))
    if not queue.is_dir():
        return 0, 0
    _safe(queue / "done")
    removed, unresolved = 0, 0
    job_name = re.compile(r"\d{20}-staff_upload-[0-9a-f]{8}\.json")
    proof_name = re.compile(r"operation-[0-9a-f]{64}\.json")
    tmp_name = re.compile(r"(.+\.json)\.\d+\.\d+\.tmp")
    with oslock.locked(_safe(queue / "submit.lock"), timeout=10):
        owners = {}
        ambiguous = set()
        def bind(name, client):
            if name in owners and owners[name] != client:
                ambiguous.add(name)
            else:
                owners[name] = client
        folders = [queue] + ([queue / "done"] if (queue / "done").is_dir() else [])
        for folder in folders:
            for path in folder.glob("*.json"):
                if not (job_name.fullmatch(path.name) or proof_name.fullmatch(path.name)):
                    continue
                try:
                    rec = _read(path)
                    if rec and rec.get("kind") == "staff_upload" and isinstance(rec.get("client"), str) and CLIENT_ID.fullmatch(rec["client"]):
                        bind(path.name, rec["client"])
                        if isinstance(rec.get("id"), str) and job_name.fullmatch(rec["id"] + ".json"):
                            bind(rec["id"] + ".json", rec["client"])
                    elif not rec or rec.get("kind") == "staff_upload" or job_name.fullmatch(path.name):
                        unresolved += 1
                except (OSError, ValueError):
                    unresolved += 1
        for folder in folders:
            for path in folder.iterdir():
                match = tmp_name.fullmatch(path.name)
                if not match or not (job_name.fullmatch(match[1]) or proof_name.fullmatch(match[1])):
                    continue
                _safe(path)
                if match[1] in ambiguous:
                    unresolved += 1
                    continue
                owner = owners.get(match[1])
                try:
                    rec = _read(path)
                except (OSError, ValueError):
                    rec = None
                if rec is not None:
                    if rec.get("kind") != "staff_upload":
                        if owner is not None:
                            unresolved += 1
                        continue
                    client = rec.get("client")
                    if not isinstance(client, str) or not CLIENT_ID.fullmatch(client) or (owner is not None and owner != client):
                        unresolved += 1
                        continue
                    owner = client
                if owner in cases:
                    path.unlink()
                    removed += 1
                elif owner is None:
                    unresolved += 1
    return removed, unresolved
