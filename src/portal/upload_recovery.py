"""Payload-bound upload receipts. Lock order: client upload lock, jobs submit lock.

An incomplete receipt is repairable, never an assertion that all effects ran.
Receipt tokens are session-memory retry handles, not document instance ids.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

import events
import oslock
from .store import _now, image_to_pdf, check_pdf
from . import capture_derivatives

TOKEN = re.compile(r"[0-9a-f]{32,64}")


def key(attempt):
    if not TOKEN.fullmatch(attempt or ""):
        raise ValueError("invalid_upload_attempt")
    return hashlib.sha256(attempt.encode()).hexdigest()


def _path(store, client, attempt):
    return store.client_dir(client) / "upload-receipts" / (key(attempt) + ".json")


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _target(value):
    return {"id": value["id"], "fingerprint": _fingerprint(value)}


def _observed(store, client, receipt):
    """Read-only outcome, including when the case ended before reconciliation."""
    record = receipt["record"]
    target = store.client_dir(client) / "uploads" / record["stored"]
    received = target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == record["sha256"] and capture_derivatives.verify(store, client, record.get("capture"))
    status = receipt["status"] if received else "pending"
    return {"status": status, "received": received, "id": record["id"] if received else None, "reading": receipt.get("reading")}


def _audit(store, client, effect, event, detail):
    """Reconcile both logs independently after a write succeeds but its reply is lost."""
    path = store.client_dir(client) / "events.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    if not any(row.get("effect") == effect for row in rows):
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"at": _now().isoformat(), "event": event, **detail, "effect": effect}) + "\n")
    action, text, _ = store._LEDGER[event]
    what = text + " [effect " + effect + "]"
    kind, case, home = store._ledger_where(client, "portal")
    if not any(row.get("what") == what and row.get("kind") == kind for row in events.rows(events.base_path(home), case=case)):
        if events.record(kind, action, what, case=case, home=home, default_who=("The client", "client", "portal")) is None:
            raise OSError("upload audit incomplete")


def _repair(store, client, receipt, path, read_now):
    record = receipt["record"]
    if not capture_derivatives.verify(store, client, record.get("capture")):
        return {"status": "pending", "received": False}
    target = store.client_dir(client) / "uploads" / record["stored"]
    if not target.exists():
        return {"status": "pending", "received": False}
    if hashlib.sha256(target.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError("upload_receipt_damaged")
    uploads = store.uploads(client)
    current = next((u for u in uploads if u["id"] == record["id"]), None)
    if current is None:
        store._write(store.client_dir(client) / "uploads.json", uploads + [record])
    elif any(current.get(k) != record[k] for k in ("stored", "sha256", "doc_id")) or current.get("capture") != record.get("capture"):
        raise ValueError("upload_receipt_damaged")
    _audit(store, client, receipt["key"], "upload", {"doc_id": record["doc_id"], "upload": record["id"], "size": record["size"]})
    requests = store.requests(client)
    for snapshot in receipt["requests"]:
        request_id = snapshot["id"]
        request = next((r for r in requests if r["id"] == request_id), None)
        if request and ((request["status"] == "open" and _fingerprint(request) == snapshot["fingerprint"]) or request.get("upload") == record["id"]):
            if request["status"] == "open":
                request.update(status="answered", answered_at=record["uploaded_at"], upload=record["id"])
                store._write(store.client_dir(client) / "requests.json", requests)
            effect = hashlib.sha256((receipt["key"] + request_id).encode()).hexdigest()
            _audit(store, client, effect, "request_answered", {"request": request_id})
    tasks = store.tasks(client)
    changed = False
    for task in tasks:
        if any(task.get("id") == snapshot["id"] and _fingerprint(task) == snapshot["fingerprint"] for snapshot in receipt["retakes"]) and not task.get("received_at"):
            task["received_at"] = record["uploaded_at"]
            changed = True
    if changed:
        store.save_tasks(client, tasks)
    if not receipt.get("queued"):
        store.enqueue(client)  # one keyed queue entry, not an append-only effect
        receipt["queued"] = True
        store._write(path, receipt)
    receipt["reading"] = read_now(client, receipt["key"])
    # "unavailable" means a reserved immediate job has no readable outcome;
    # do not create another job or claim the follow-up finished.
    receipt["status"] = "incomplete" if receipt["reading"] == "unavailable" else "complete"
    store._write(path, receipt)
    return {"status": receipt["status"], "received": True, "id": record["id"], "reading": receipt["reading"]}


def accept(store, client, attempt, doc_id, filename, data, kind, read_now, *, capture=None, derivative=None, converted=None):
    path = _path(store, client, attempt)
    if kind == "application/pdf" and converted is not data:
        check_pdf(data)
    with store._lock, oslock.locked(store.client_dir(client) / "upload.lock", timeout=10):
        digest = hashlib.sha256(data).hexdigest()
        receipt = store._read(path, None)
        if receipt and not receipt.get("capture_binding") and capture and capture.get("policy") == "original-file-v1":
            capture = None  # legacy image receipt keeps its original contract
        # Historical receipts remain replay-compatible; all new image uploads
        # retain the original even when chosen through ordinary file fallback.
        if capture is None and kind != "application/pdf" and (receipt is None or receipt.get("capture_binding")):
            capture = capture_derivatives.original_attachment(data)
        if receipt and (receipt["source_sha256"] != digest or receipt["record"]["doc_id"] != doc_id or receipt.get("capture_binding") != capture_derivatives.digest(capture)):
            raise ValueError("upload_attempt_conflict")
        if receipt and receipt["status"] == "complete":
            if not capture or _observed(store, client, receipt)["received"]:
                return {"status": "complete", "received": True, "id": receipt["record"]["id"], "reading": receipt.get("reading")}
        converted = converted if converted is not None else data if kind == "application/pdf" else image_to_pdf(data)
        if receipt is None:
            requests = [_target(r) for r in store.requests(client) if r["status"] == "open" and r.get("doc_id") == doc_id]
            retakes = [_target(t) for t in store.tasks(client) if t.get("kind") == "retake" and t.get("doc_id") == doc_id and not t.get("received_at")]
            stored = doc_id + "-" + key(attempt)[:32] + ".pdf"
            record = {"id": stored[:-4], "doc_id": doc_id, "filename": filename[:120], "stored": stored,
                      "size": len(converted), "sha256": hashlib.sha256(converted).hexdigest(), "source_sha256": digest,
                      "uploaded_at": _now().isoformat(), "status": "received", **({"retake": True} if retakes else {})}
            if capture:
                record["capture"] = capture_derivatives.bind(capture, record["id"])
            receipt = {"version": 1, "key": key(attempt), "source_sha256": digest, "record": record, "requests": requests,
                       "retakes": retakes, "status": "pending", "queued": False, "reading": None}
            if capture:
                receipt["capture_binding"] = capture_derivatives.digest(capture)
            store._write(path, receipt)  # planned identity exists before the file can be committed
        if capture:
            capture_derivatives.persist(store, client, receipt["record"]["capture"], data, derivative)
        target = store.client_dir(client) / "uploads" / receipt["record"]["stored"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            if hashlib.sha256(converted).hexdigest() != receipt["record"]["sha256"]:
                raise ValueError("upload_receipt_damaged")
            temp = target.with_suffix(".part")
            temp.write_bytes(converted)
            os.replace(temp, target)
        return _repair(store, client, receipt, path, read_now)


def outcome(store, client, attempt, read_now, repair=True):
    path = _path(store, client, attempt)
    if not repair:
        with store._lock:
            receipt = store._read(path, None)
            return {"status": "unknown", "received": False} if receipt is None else _observed(store, client, receipt)
    with store._lock, oslock.locked(store.client_dir(client) / "upload.lock", timeout=10):
        receipt = store._read(path, None)
        if receipt is None:
            return {"status": "unknown", "received": False}
        if receipt["status"] == "complete":
            if not receipt["record"].get("capture") or _observed(store, client, receipt)["received"]:
                return {"status": "complete", "received": True, "id": receipt["record"]["id"], "reading": receipt.get("reading")}
        return _repair(store, client, receipt, path, read_now)
