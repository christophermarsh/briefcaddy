"""Configured-root query and publication adapters for the existing JSON job queue.

This preserves transition file semantics. It does not implement leases, claims,
execution authority or PostgreSQL/Service Bus durability.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

JOB_FILE_PATTERN = re.compile(r"\d{20}-[a-z_]+-[0-9a-f]{8}\.json")


def read_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def active_files(root: Path, pattern=JOB_FILE_PATTERN) -> list[Path]:
    try:
        return sorted(p for p in root.iterdir() if pattern.fullmatch(p.name))
    except OSError:
        return []


class FilesystemJobQueries:
    def __init__(self, root: str | Path, *, reader: Callable = read_record,
                 active: Callable | None = None, pattern=JOB_FILE_PATTERN,
                 now: Callable = time.time):
        self.root = Path(root)
        self.reader = reader
        self.pattern = pattern
        self.active = active if active is not None else lambda root: active_files(root, pattern)
        self.now = now

    def get(self, job_id: str, *, active_only: bool = False) -> dict[str, Any] | None:
        if not self.pattern.fullmatch(job_id + ".json"):
            return None
        current = self.reader(self.root / f"{job_id}.json")
        if active_only:
            return current
        return current or self.reader(self.root / "done" / f"{job_id}.json")

    def list_jobs(self, client: str | None = None, kind: str | None = None,
                  recent: float = 600) -> list[dict[str, Any]]:
        out = [j for j in (self.reader(p) for p in self.active(self.root)) if j]
        done = self.root / "done"
        if done.is_dir():
            cutoff = self.now() - recent
            for p in sorted(p for p in done.iterdir() if self.pattern.fullmatch(p.name))[-200:]:
                try:
                    if p.stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue
                j = self.reader(p)
                if j:
                    out.append(j)
        return [j for j in out if (client is None or j.get("client") == client)
                and (kind is None or j.get("kind") == kind)]


class FilesystemJobPublication:
    """Legacy ordered publication/completion, using the composition's primitives.

    This is not a transactional outbox or a distributed lease/fence.
    """

    def __init__(self, root, *, read, write, durable_write, get, locked, stamp,
                 time_ns, pattern):
        self.root = Path(root)
        self.read, self.write, self.durable_write = read, write, durable_write
        self.get, self.locked, self.stamp = get, locked, stamp
        self.time_ns, self.pattern = time_ns, pattern

    def reserve_and_publish(self, kind, client, by, args, operation_id, *, recover_reserved=False):
        root = self.root
        if not re.fullmatch(r"[0-9a-f]{32,64}", operation_id):
            raise ValueError("invalid operation id")
        scope = hashlib.sha256(json.dumps([kind, client, operation_id], separators=(",", ":")).encode()).hexdigest()
        payload = hashlib.sha256(json.dumps(args or {}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        proof = root / ("operation-" + scope + ".json")
        # Receipt/client lock precedes submit.lock. Proof remains until the client
        # is purged; normal 14-day job cleanup must not allow a second submission.
        with self.locked(root / "submit.lock", timeout=10):
            old = self.read(proof)
            if recover_reserved and proof.exists() and not old:
                raise ValueError("Operation reservation is damaged")
            if old:
                if recover_reserved and (not isinstance(old.get("id"), str) or not self.pattern().fullmatch(old["id"] + ".json") or not isinstance(old.get("created"), str)):
                    raise ValueError("Operation reservation is damaged")
                if recover_reserved:
                    if datetime.fromisoformat(old["created"]).tzinfo is None:
                        raise ValueError("Operation reservation is damaged")
                if old.get("payload") != payload or old.get("client") != client or old.get("kind") != kind or (recover_reserved and (not isinstance(old.get("state"), str) or old["state"] not in {"reserved", "published"})):
                    raise ValueError("operation payload changed")
                existing = self.get(old["id"])
                if existing:
                    if existing.get("client") != client or existing.get("kind") != kind or existing.get("args") != (args or {}):
                        raise ValueError("operation job changed")
                    if recover_reserved:
                        if old.get("recovery_version") != 1 or existing.get("operation_scope") != scope:
                            raise ValueError("Operation reservation changed")
                        if old["state"] == "reserved":
                            self.durable_write(proof, old | {"state": "published"})
                    return existing
                if not (recover_reserved and old.get("recovery_version") == 1 and old.get("state") == "reserved"):
                    return {**old, "state": "unavailable"}  # published/pruned and legacy proofs never reconstruct
                job_id = old["id"]
                created = old["created"]
            else:
                job_id = f"{self.time_ns():020d}-{kind}-{scope[:8]}"
                created = self.stamp()
                old = {"id": job_id, "kind": kind, "client": client, "payload": payload, "state": "reserved"}
                if recover_reserved:
                    old |= {"recovery_version": 1, "created": created}
                (self.durable_write if recover_reserved else self.write)(proof, old)
            job = {"id": job_id, "kind": kind, "client": client, "by": by or None, "state": "queued", "created": self.stamp(), "started": None,
                   "finished": None, "progress": {"step": 0, "steps": 0, "text": ""}, "args": args or {}, "result": None, "error": None, "pid": None}
            if recover_reserved:
                job |= {"operation_scope": scope, "created": created}
            (self.durable_write if recover_reserved else self.write)(root / f"{job_id}.json", job)
            if recover_reserved:
                self.durable_write(proof, old | {"state": "published"})
            return job

    def finish(self, job, state, fields):
        job.update(state=state, finished=self.stamp(), **fields)
        done = self.root / "done"
        done.mkdir(parents=True, exist_ok=True)
        self.write(done / f"{job['id']}.json", job)
        (self.root / f"{job['id']}.json").unlink(missing_ok=True)
