"""Bridge portal generations into the installed worker; no outbound messages.

Lock order: case lock (processing only), queue lock, jobs submit lock.
The queue lock is never held through pipeline work. A crash after pipeline
writes but before the processed receipt can require replay, not exactly-once
processing. Newer generations are never acknowledged by older work.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
from datetime import datetime
from pathlib import Path

from law_app.adapters.persistence.filesystem.portal import _atomic, _safe

import clock
import oslock

TOKEN = re.compile(r"[0-9a-f]{32,64}")
MAX_ATTEMPTS = 3
RETRY_SECONDS = 30
BATCH = 32


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _queue_path(store, client):
    store.client_dir(client)  # existing strict client id validation
    return _safe(store.root / "queue" / client)


def _lock(store):
    return oslock.locked(_safe(store.root / "queue" / ".lock"), timeout=10)


def _generation(store, client):
    path = _queue_path(store, client)
    if not path.exists():
        return None
    if path.stat().st_size > 4096:
        raise ValueError("Portal queue marker is damaged")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Portal queue marker is damaged")
    text = path.read_text(encoding="utf-8").strip()
    if not text.startswith("{"):
        # Older timestamp markers remain usable, with a stable retry identity.
        stamp = datetime.fromisoformat(text.strip())
        if stamp.tzinfo is None:
            raise ValueError("Portal queue marker is damaged")
        return {"version": 1, "generation": _digest([client, text]), "at": stamp.isoformat(), "legacy": True}
    value = json.loads(text)
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("generation"), str) or not TOKEN.fullmatch(value["generation"]):
        raise ValueError("Portal queue marker is damaged")
    if not isinstance(value.get("at"), str) or datetime.fromisoformat(value["at"]).tzinfo is None:
        raise ValueError("Portal queue marker is damaged")
    return value


def generation(store, client):
    with _lock(store):
        return _generation(store, client)


def enqueue(store, client):
    marker = {"version": 1, "generation": secrets.token_hex(16), "at": clock.stamp()}
    with _lock(store):
        if not _safe(store.client_dir(client) / "profile.json").is_file():
            raise LookupError("unknown client")
        _atomic(_queue_path(store, client), marker, stage_prefix="portal-queue-" + hashlib.sha256(client.encode()).hexdigest())
    return marker


def _ack(store, client, expected):
    current = _generation(store, client)
    if current is None:
        return "absent"
    if current["generation"] != expected:
        return "newer_generation_retained"
    path = _queue_path(store, client)
    path.unlink()
    if os.name != "nt":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return "acknowledged"


def acknowledge(store, client, expected):
    if not isinstance(expected, str) or not TOKEN.fullmatch(expected):
        raise ValueError("Invalid portal queue generation")
    with _lock(store):
        return _ack(store, client, expected)


def _paths(ctx, client, token):
    store = ctx.store()
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError("Invalid portal queue generation")
    _safe(ctx.clients)
    _safe(ctx.root)
    folder = _safe(store.client_dir(client))
    if not _safe(folder / "profile.json").is_file():
        raise LookupError("unknown client")
    return store, _safe(folder / "processing-receipts" / (token + ".json"))


def _identity(ctx, client, token):
    return {"client": client, "generation": token, "clients_root": str(_safe(ctx.clients)),
            "portal_root": str(_safe(ctx.portal)), "jobs_root": str(_safe(ctx.root)), "use_policies": ctx.use_policies}


def _read(path, identity):
    if not path.exists():
        return {"version": 1, "identity": identity, "state": "pending", "attempts": [], "created": clock.stamp()}
    if path.stat().st_size > 65536:
        raise ValueError("Portal processing receipt is damaged")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("identity") != identity or not isinstance(value.get("state"), str) or value["state"] not in {"pending", "processing", "failed", "processed", "acked", "superseded"}:
        raise ValueError("Portal processing receipt changed or is damaged")
    attempts = value.get("attempts")
    if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS or any(not isinstance(a, dict) or not isinstance(a.get("operation"), str) or not TOKEN.fullmatch(a["operation"]) for a in attempts):
        raise ValueError("Portal processing receipt is damaged")
    for attempt in attempts:
        if "job" in attempt and (not isinstance(attempt["job"], str) or not re.fullmatch(r"\d{20}-portal_process-[0-9a-f]{8}", attempt["job"])):
            raise ValueError("Portal processing receipt is damaged")
        retry = attempt.get("retry_at", 0)
        if isinstance(retry, bool) or not isinstance(retry, (int, float)) or not math.isfinite(retry) or retry < 0:
            raise ValueError("Portal processing receipt is damaged")
    if value["state"] != "pending" and not attempts:
        raise ValueError("Portal processing receipt is damaged")
    if value["state"] in {"processed", "acked"}:
        if not attempts[-1].get("job") or not isinstance(value.get("outcome"), dict):
            raise ValueError("Portal completion proof is missing")
        for key in ("input_snapshot_before", "input_snapshot_after"):
            if not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
                raise ValueError("Portal completion proof is missing")
        for key in ("processing_at", "processed_at"):
            if not isinstance(value.get(key), str) or datetime.fromisoformat(value[key]).tzinfo is None:
                raise ValueError("Portal completion proof is missing")
    return value


def _ack_receipt(store, client, receipt, path):
    receipt["acknowledgment"] = _ack(store, client, receipt["identity"]["generation"])
    receipt.update(state="acked", acknowledged_at=clock.stamp())
    _atomic(path, receipt)
    return receipt


def _attempt(ctx, store, client, marker):
    import jobs
    token = marker["generation"]
    store, path = _paths(ctx, client, token)
    identity = _identity(ctx, client, token)
    receipt = _read(path, identity)
    if receipt["state"] in {"processed", "acked"}:
        attempt = receipt["attempts"][-1]
        _bind_job(ctx, {"id": attempt["job"], "client": client, "kind": "portal_process",
                       "args": identity | {"attempt": len(receipt["attempts"])},
                       "operation_scope": _digest(["portal_process", client, attempt["operation"]])}, receipt, identity)
        return _ack_receipt(store, client, receipt, path)
    if receipt["state"] == "superseded":
        return receipt
    attempts = receipt["attempts"]
    if attempts:
        attempt = attempts[-1]
        args = identity | {"attempt": len(attempts)}
        old = jobs.submit(ctx.root, "portal_process", client, by="Portal intake queue", args=args, operation_id=attempt["operation"], recover_reserved=True)
        if old["state"] in {"queued", "running"}:
            attempt["job"] = old["id"]
            _atomic(path, receipt)
            return receipt
        # A done job without its processed receipt cannot prove complete intake
        # effects. Retain visible failure; a bounded new attempt may replay work.
        receipt.update(state="failed", last_job_state=old["state"])
        if "retry_at" not in attempt:
            attempt["retry_at"] = time.time() + RETRY_SECONDS
        _atomic(path, receipt)
        if len(attempts) >= MAX_ATTEMPTS or time.time() < attempt["retry_at"]:
            return receipt
    operation = _digest([identity, len(attempts) + 1])
    attempts.append({"operation": operation, "created": clock.stamp()})
    receipt.update(state="pending", replay_possible=bool(len(attempts) > 1))
    _atomic(path, receipt)  # operation binding exists before submission
    args = identity | {"attempt": len(attempts)}
    job = jobs.submit(ctx.root, "portal_process", client, by="Portal intake queue", args=args, operation_id=operation, recover_reserved=True)
    attempts[-1]["job"] = job["id"]
    _atomic(path, receipt)
    return receipt


def bridge(ctx, limit=BATCH):
    """One bounded, rotating pass; bad clients cannot stop other-client work."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= BATCH:
        raise ValueError("Invalid portal queue batch size")
    if ctx.portal is None:
        return []
    store = ctx.store()
    _safe(store.root / "queue")
    clients = store.queued()
    if not clients:
        return []
    offset = getattr(ctx, "queue_cursor", 0) % len(clients)
    chosen = (clients[offset:] + clients[:offset])[:limit]
    ctx.queue_cursor = offset + len(chosen)
    outcomes = []
    for client in chosen:
        try:
            with _lock(store):
                marker = _generation(store, client)
                if marker:
                    receipt = _attempt(ctx, store, client, marker)
                    outcomes.append({"client": client, "state": receipt["state"], "attempts": len(receipt["attempts"])})
        except (ValueError, OSError, LookupError, TimeoutError):
            outcomes.append({"client": client, "state": "unavailable"})
    return outcomes


def current_job(ctx, client):
    """Published queued job for the current generation, for legacy upload handoff."""
    import jobs
    store = ctx.store()
    with _lock(store):
        marker = _generation(store, client)
        if marker is None:
            return None, None
        receipt = _attempt(ctx, store, client, marker)
        if receipt["state"] in {"failed", "acked", "superseded"} or not receipt["attempts"]:
            return marker, None
        attempt = receipt["attempts"][-1]
        job = jobs.get(ctx.root, attempt.get("job", ""))
        if not job or job.get("state") != "queued":
            return marker, None
        _bind_job(ctx, job, receipt, _identity(ctx, client, marker["generation"]))
        return marker, job


def _open_case(ctx, store, client):
    """Fresh lifecycle checks under the case lock, including absent purged cases."""
    import engagement
    import conflicts
    import purge
    folder = _safe(ctx.clients / client)
    def read(path, default):
        path = _safe(path)
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        if not isinstance(value, dict):
            raise ValueError("Case lifecycle state is unavailable")
        return value
    rec = read(folder / engagement.FILE, {})
    end = rec.get("end")
    if end is not None and (not isinstance(end, dict) or end.get("state") not in engagement.ENDED):
        raise ValueError("Case lifecycle state is unavailable")
    conflict = read(folder / conflicts.FILE, {})
    decision = conflict.get("decision")
    if decision is not None and (not isinstance(decision, dict) or decision.get("decision") not in conflicts.DECISIONS):
        raise ValueError("Case lifecycle state is unavailable")
    purges = read(ctx.clients.parent / purge.PURGES_FILE, {"cases": {}}).get("cases")
    destroyed = read(ctx.clients.parent / engagement.DESTROYED_FILE, {"cases": []}).get("cases")
    def case_name(value):
        return (isinstance(value, str) and bool(value.strip()) and value not in {".", ".."}
                and "/" not in value and "\\" not in value and Path(value).name == value
                and not any(ord(c) < 32 or ord(c) == 127 for c in value))
    if (not isinstance(purges, dict) or not isinstance(destroyed, list)
            or any(not isinstance(r, dict) or not case_name(r.get("case")) for r in destroyed)):
        raise ValueError("Case destruction state is unavailable")
    entry = purges.get(client, {})
    if client in purges and (not isinstance(entry, dict) or not isinstance(entry.get("state"), str) or entry["state"] not in {"waiting", "done", "cancelled"}):
        raise ValueError("Case destruction state is unavailable")
    if (store.stopped(client) or end or rec.get("destroyed") or conflict.get("abandoned")
            or (decision or {}).get("decision") == "declined"
            or entry.get("state") in {"waiting", "running", "done", "purged"}
            or any(row.get("case") == client for row in destroyed)):
        raise ValueError("This client's case is not open for processing; the queue is retained")


def _snapshot(store, client):
    hashes = {}
    for name in ("profile.json", "answers.json", "uploads.json", "requests.json"):
        path = _safe(store.client_dir(client) / name)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return _digest(hashes)


def _bind_job(ctx, job, receipt, identity):
    import jobs
    attempts = receipt["attempts"]
    if not attempts or job.get("args") != identity | {"attempt": len(attempts)}:
        raise ValueError("Portal processing job changed")
    operation = _digest([identity, len(attempts)])
    scope = _digest(["portal_process", job["client"], operation])
    if attempts[-1]["operation"] != operation or job.get("operation_scope") != scope or not jobs._published(ctx.root, job):
        raise ValueError("Portal processing job changed")
    if attempts[-1].get("job") not in (None, job["id"]):
        raise ValueError("Portal processing job changed")
    attempts[-1]["job"] = job["id"]


def repair_completed(ctx, job):
    """Recover only an actual processed receipt, including after queue unlink."""
    args = job.get("args")
    if not isinstance(args, dict):
        raise ValueError("Portal processing job changed")
    client, token = job["client"], args.get("generation")
    store, path = _paths(ctx, client, token)
    identity = _identity(ctx, client, token)
    with _lock(store):
        receipt = _read(path, identity)
        _bind_job(ctx, job, receipt, identity)
        if receipt["state"] not in {"processed", "acked"}:
            return None
        if receipt["state"] == "processed":
            _ack_receipt(store, client, receipt, path)
        return {"processed": True, "acknowledgment_repaired": True}


def process(ctx, job, progress):
    """Called under the normal job case lock. No queue lock during processing."""
    from .engine import process_client
    args, client = job["args"], job["client"]
    if not isinstance(args, dict):
        raise ValueError("Portal processing job changed")
    token = args.get("generation")
    store, path = _paths(ctx, client, token)
    identity = _identity(ctx, client, token)
    with _lock(store):
        receipt = _read(path, identity)
        _bind_job(ctx, job, receipt, identity)
        _atomic(path, receipt)
        if receipt["state"] == "processed":
            _ack_receipt(store, client, receipt, path)
            return {"processed": True, "acknowledgment_repaired": True}
        if receipt["state"] == "acked":
            return {"processed": True, "already_acknowledged": True}
        current = _generation(store, client)
        if current is None or current["generation"] != token:
            receipt.update(state="superseded", superseded_at=clock.stamp())
            _atomic(path, receipt)
            return {"processed": False, "superseded": True}
        _open_case(ctx, store, client)
        receipt.update(state="processing", processing_at=clock.stamp(), input_snapshot_before=_snapshot(store, client))
        _atomic(path, receipt)
    progress(1, 2, "Reading the client's answers and documents")
    # Product processing retains all existing boundary/subject/field holds.
    # There is deliberately no Notifier or outbound result sink in this path.
    result = process_client(store, client, out_root=ctx.clients, use_policies=ctx.use_policies)
    meta = json.loads(_safe(ctx.clients / client / "meta.json").read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or meta.get("client_id") != client or meta.get("errors"):
        raise ValueError("The client's processing did not complete; the queue remains pending")
    documents_path = _safe(ctx.clients / client / "documents.json")
    documents = json.loads(documents_path.read_text(encoding="utf-8"))
    if not isinstance(documents, dict) or not isinstance(documents.get("documents"), list) or documents.get("boundary_processing") or documents.get("boundary_failure"):
        raise ValueError("Document processing is incomplete; the queue remains pending")
    with _lock(store):
        receipt["input_snapshot_after"] = _snapshot(store, client)
        receipt["input_changed_during_processing"] = receipt["input_snapshot_before"] != receipt["input_snapshot_after"]
        current = _generation(store, client)
        receipt["queue_generation_after"] = current["generation"] if current else None
        receipt["newer_generation_observed"] = bool(current and current["generation"] != token)
        receipt.update(state="processed", processed_at=clock.stamp(), outcome={"tasks": result.get("tasks"), "counts": {k: result.get(k) for k in ("blocking", "review", "informational")}})
        _atomic(path, receipt)  # successful processing proof precedes ack
        _ack_receipt(store, client, receipt, path)
    progress(2, 2, "Updating the case and the portal")
    return {"processed": True, "acknowledgment": receipt["acknowledgment"], "input_changed_during_processing": receipt["input_changed_during_processing"]}
