"""Explicit selected Drive intake for existing authorized cases.

No routes, scheduler, credentials or provider calls run on import. The trusted
caller supplies the current installation, actor/ACL/config readers and worker
wakeup. A worker rechecks all authority and the exact preview before copying.
This source never publishes results or invokes a notifier.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable

import events
import jobs
import oslock

from . import sync

READONLY = "https://www.googleapis.com/auth/drive.readonly"
CASE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,100}")
REMOTE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
MAX_SELECTED = 100
MAX_MAPPED_CASES = 2000
MAX_DOCUMENTS = 1000
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024


class IntakeError(ValueError):
    pass


class ProcessingFailed(IntakeError):
    """Copied source documents remain; the durable phase report identifies them."""


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def retained_sources(folder: Path) -> dict:
    out = {}
    for path in sorted(folder.glob("*.pdf")):
        sync._plain(path)
        out[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def durable_record(path: Path):
    sync._plain(path)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IntakeError("Durable intake state is damaged; no replacement or success is assumed.") from exc
    if not isinstance(value, dict):
        raise IntakeError("Durable intake state is not a valid record.")
    return value


@dataclass(frozen=True)
class FirmScope:
    """Construct only from the server/worker's trusted installation configuration."""
    root: Path

    def __post_init__(self):
        root = Path(self.root).absolute()
        sync._plain(root)
        object.__setattr__(self, "root", root.resolve())
        self.validate()

    @property
    def data(self):
        return self.root / "data"

    @property
    def cases(self):
        return self.data / "clients"

    @property
    def documents(self):
        return self.root / "clients"

    @property
    def queue(self):
        return self.data / "jobs"

    @property
    def portal(self):
        return self.data / "portal"

    def validate(self):
        if not (self.root / "install/install.json").is_file():
            raise IntakeError("Use the current prepared installation.")
        for path in (self.root / "install/install.json", self.data, self.cases, self.documents, self.queue,
                     self.portal, self.data / "review_users.json", self.queue / "drive-receipts"):
            sync._plain(path)
            if not path.resolve().is_relative_to(self.root):
                raise IntakeError("Intake stores must belong to this installation.")

    def case(self, case: str):
        if not isinstance(case, str) or not CASE_ID.fullmatch(case):
            raise IntakeError("Choose an existing local case.")
        folder = self.cases / case
        sync._plain(folder)
        import source_association
        source_association.assert_ready(folder)
        if not (folder / "fact_graph.json").is_file():
            raise IntakeError("Process/enroll this case through the protected front desk before linking Drive.")
        # Reuse the existing assignment lifecycle gate, including Q1 wait and
        # destruction state; never resurrect an ended/declined/purged record.
        from case_assignment import Assignments
        Assignments(self.cases, self.queue, lambda: [])._open(folder)
        source = self.documents / case / "source"
        sync._plain(source)
        source_association.verify(folder, source)
        # A full reread may only use the existing complete source association;
        # never replace a portal or another folder's evidence with Drive-only PDFs.
        meta = folder / "meta.json"
        sync._plain(meta)
        if meta.exists():
            value = json.loads(meta.read_text(encoding="utf-8"))
            associated = value.get("source_folder")
            if not associated or Path(associated).absolute() != source:
                raise IntakeError("This case uses another source folder; use a reviewed incremental import instead.")
        elif any(folder.iterdir()):
            raise IntakeError("This case has no verified source association; it cannot be reread from Drive.")
        for path in (source, source.parent):
            if not path.resolve().is_relative_to(self.documents):
                raise IntakeError("The selected source folder leaves this installation.")
        if source.exists():
            for path in source.iterdir():
                sync._plain(path)
        return folder, source


class _SelectedSource:
    """Frozen selected-only adapter. Its interface contains no result sink."""
    name = "google_drive"

    def __init__(self, source, clients, documents):
        self.source, self.selected, self.listings = source, clients, documents

    def clients(self):
        return self.selected

    def documents(self, client):
        return self.listings[client.id]

    def download(self, client, doc):
        data = self.source.download(client, doc)
        if not isinstance(data, bytes) or not data or len(data) > MAX_DOCUMENT_BYTES:
            raise IntakeError("A source document is empty or exceeds the intake limit.")
        # Native Google exports have no MD5 for exported PDF bytes. Ordinary
        # Drive files with MD5 are refused if the bytes changed after listing.
        checksum = str(doc.raw.get("md5Checksum") or "")
        if re.fullmatch(r"[0-9a-fA-F]{32}", checksum) and hashlib.md5(data).hexdigest().lower() != checksum.lower():
            raise IntakeError("A source document changed during download; preview it again.")
        return data


class SelectedIntake:
    def __init__(self, scope: FirmScope, settings_reader: Callable, source_factory: Callable,
                 actor_reader: Callable, may_access: Callable):
        self.scope, self.settings_reader, self.source_factory = scope, settings_reader, source_factory
        self.actor_reader, self.may_access = actor_reader, may_access

    def _authority(self, email, mapping):
        self.scope.validate()
        for name, expected in {"I485_EVENTS": self.scope.data / "events.jsonl", "I485_READER_EXAMPLES": self.scope.data / "reader_examples"}.items():
            if os.environ.get(name) and Path(os.environ[name]).absolute() != expected:
                raise IntakeError("The intake worker inherits another installation's audit/evidence stores.")
            sync._plain(expected)
        actor = self.actor_reader(email)
        if not isinstance(actor, dict) or actor.get("email") != email or actor.get("role") not in ("attorney", "paralegal") or not actor.get("active", True):
            raise IntakeError("A current active staff account is required.")
        for case in mapping.values():
            folder, _ = self.scope.case(case)
            if not self.may_access(actor, folder):
                raise IntakeError("A selected local case is unavailable to this account.")
        return actor

    def _mapping_binding(self, selected, mapping):
        """Current operator configuration, never a body-supplied access grant.

        Validate the whole bounded map without opening other cases. Only the
        selected pairs reach case ACL checks; snapshots contain an opaque
        whole-map digest, never the other cases' IDs or names.
        """
        configured = self.settings_reader()
        record = configured.get("intake_mapping") if isinstance(configured, dict) else None
        if (not isinstance(record, dict) or set(record) != {"version", "revision", "bindings"}
                or type(record.get("version")) is not int or record["version"] != 1
                or type(record.get("revision")) is not int or record["revision"] < 1):
            raise IntakeError("Configure a versioned selected Drive intake mapping in Connections before previewing.")
        bindings = record.get("bindings")
        if (not isinstance(bindings, dict) or not bindings or len(bindings) > MAX_MAPPED_CASES
                or any(not isinstance(remote, str) or not REMOTE_ID.fullmatch(remote)
                       or not isinstance(case, str) or not CASE_ID.fullmatch(case)
                       for remote, case in bindings.items())
                or len(set(bindings.values())) != len(bindings)):
            raise IntakeError("The configured Drive intake mapping is missing, invalid or exceeds its bounded case limit.")
        if any(bindings.get(remote) != mapping[remote] for remote in selected):
            raise IntakeError("A selected Drive mapping changed or is not configured for this case; preview again.")
        canonical = {"version": 1, "revision": record["revision"], "bindings": dict(sorted(bindings.items()))}
        return {"version": 1, "revision": record["revision"], "digest": digest(canonical)}

    def _selection(self, selected, mapping):
        if not isinstance(selected, list) or not selected or len(selected) > MAX_SELECTED:
            raise IntakeError("Select a nonempty bounded list of Drive clients.")
        if any(not isinstance(v, str) or not REMOTE_ID.fullmatch(v) for v in selected) or len(set(selected)) != len(selected):
            raise IntakeError("Selected Drive IDs must be valid and unique.")
        if not isinstance(mapping, dict) or set(mapping) != set(selected):
            raise IntakeError("Map each selected Drive client to a different existing local case.")
        if any(not isinstance(v, str) or not CASE_ID.fullmatch(v) for v in mapping.values()):
            raise IntakeError("Invalid local case mapping.")
        if len(set(mapping.values())) != len(mapping):
            raise IntakeError("Different Drive clients cannot share a local case.")
        self._mapping_binding(selected, mapping)
        return sorted(selected)

    def _settings(self):
        configured = self.settings_reader()
        if not isinstance(configured, dict) or not isinstance(configured.get("root_folder_id"), str) or not REMOTE_ID.fullmatch(configured["root_folder_id"]):
            raise IntakeError("Configure an explicit Drive root before previewing clients.")
        sub = configured.get("documents_subfolder")
        if sub is not None and (not isinstance(sub, str) or not sub.strip() or len(sub) > 200):
            raise IntakeError("Invalid configured documents subfolder.")
        return {"root_folder_id": configured["root_folder_id"], "documents_subfolder": sub, "results_folder_name": None}

    @contextmanager
    def _source(self, settings):
        source = self.source_factory(dict(settings), write=False)
        try:
            if source.name != "google_drive" or source.scope != READONLY or source.s.get("results_folder_name") is not None or any(source.s.get(k) != v for k, v in settings.items()):
                raise IntakeError("Selected intake requires a matching read-only Drive source with no result sink.")
            yield source
        finally:
            if getattr(source, "http", None) is not None:
                source.http.close()

    def _snapshot(self, source, settings, email, selected, mapping):
        mapping_binding = self._mapping_binding(selected, mapping)
        self._authority(email, mapping)
        clients = source.clients()
        ids = [c.id for c in clients]
        if len(ids) != len(set(ids)) or not set(selected).issubset(ids):
            raise IntakeError("A selected client is no longer a unique child of the configured Drive root.")
        picked = sorted([c for c in clients if c.id in selected], key=lambda c: c.id)
        listings, items = {}, []
        for client in picked:
            documents = source.documents(client)
            if len(documents) > MAX_DOCUMENTS or len({d.id for d in documents}) != len(documents):
                raise IntakeError("A selected document listing is ambiguous or exceeds the intake limit.")
            names = [sync._filename(d).casefold() for d in documents if sync._kind(d)]
            if len(names) != len(set(names)):
                raise IntakeError("Selected document names would collide locally.")
            for doc in documents:
                if not isinstance(doc.id, str) or not REMOTE_ID.fullmatch(doc.id):
                    raise IntakeError("Invalid source document ID.")
                sync._plain(self.scope.documents / mapping[client.id] / "source" / sync._filename(doc))
            listings[client.id] = documents
            items.append({"remote": client.id, "name": client.name, "local": mapping[client.id],
                          "documents": sorted([{"id": d.id, "name": d.name, "mime": d.mime, "fingerprint": d.fingerprint(),
                                                "native_mime": d.raw.get("mimeType"), "md5": d.raw.get("md5Checksum"),
                                                "readable": bool(sync._kind(d))} for d in documents], key=lambda d: d["id"])})
        body = {"format": "selected-drive-1", "firm_root": str(self.scope.root), "actor": email, "settings": settings,
                "selected": selected, "mapping": dict(sorted(mapping.items())), "mapping_binding": mapping_binding, "clients": items}
        return body, _SelectedSource(source, picked, listings)

    def preview(self, email: str, selected: list[str], mapping: dict[str, str]):
        selected = self._selection(selected, mapping)
        self._authority(email, mapping)  # authorization precedes any provider listing
        settings = self._settings()
        with self._source(settings) as source:
            body, _ = self._snapshot(source, settings, email, selected, mapping)
        return {"snapshot": body, "digest": digest(body), "writes_to_drive": False, "processing": "not queued"}

    def _validated_preview(self, email, preview):
        if not isinstance(preview, dict) or not isinstance(preview.get("snapshot"), dict):
            raise IntakeError("Preview the selection first.")
        body = preview["snapshot"]
        if body.get("format") != "selected-drive-1" or body.get("firm_root") != str(self.scope.root) or body.get("actor") != email or digest(body) != preview.get("digest"):
            raise IntakeError("The preview does not belong to this installation and actor.")
        self._selection(body.get("selected"), body.get("mapping"))
        self._authority(email, body["mapping"])
        binding = body.get("mapping_binding")
        if (not isinstance(binding, dict) or set(binding) != {"version", "revision", "digest"}
                or type(binding.get("version")) is not int or binding["version"] != 1
                or type(binding.get("revision")) is not int or binding["revision"] < 1
                or not isinstance(binding.get("digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", binding["digest"])
                or binding != self._mapping_binding(body["selected"], body["mapping"])):
            raise IntakeError("Drive intake mapping changed; preview the selection again.")
        if body.get("settings") != self._settings():
            raise IntakeError("Drive configuration changed; preview the selection again.")
        return body

    def enqueue(self, email: str, preview: dict, attempt: str, wake_worker: Callable):
        body = self._validated_preview(email, preview)
        # Same order as purge/worker: case(s), then group, then queue submit.
        # Deterministic acquisition prevents overlapping selections deadlocking.
        with ExitStack() as locks:
            for case in sorted(body["mapping"].values()):
                locks.enter_context(jobs.case_lock(self.scope.queue, case, timeout=10))
            self._authority(email, body["mapping"])
            pending = self._enqueue_locked(email, preview, attempt)
        started = bool(wake_worker(self.scope))
        return {"jobs": pending, "worker_available": started, "completed": False}

    def _enqueue_locked(self, email: str, preview: dict, attempt: str):
        body = self._validated_preview(email, preview)
        if not isinstance(attempt, str) or not re.fullmatch(r"[0-9a-f]{32,64}", attempt):
            raise IntakeError("Supply a durable intake attempt ID.")
        if "drive_intake" not in jobs.KINDS:
            raise IntakeError("This installed worker does not yet support selected Drive intake.")
        actor = self._authority(email, body["mapping"])
        operation = digest({"firm": str(self.scope.root), "actor": email, "attempt": attempt})
        proof = self.scope.queue / "drive-receipts" / (operation + ".json")
        sync._plain(proof)
        with oslock.locked(proof.with_suffix(".lock"), timeout=10):
            old = durable_record(proof)
            if old is not None and (old.get("version") != 1 or old.get("type") != "group" or not re.fullmatch(r"[0-9a-f]{64}", str(old.get("digest") or "")) or old.get("state") not in ("reserving", "reserved") or not isinstance(old.get("jobs"), dict)):
                raise IntakeError("Durable intake receipt is incomplete; no replacement is assumed.")
            if old is not None and old.get("retired") is not False:
                raise IntakeError("This intake group was retired by case cleanup; no submission is recreated.")
            if old is not None and old.get("digest") != preview["digest"]:
                raise IntakeError("The same intake attempt cannot change its preview or selection.")
            pending = {remote: {"client": body["mapping"][remote], "id": None, "state": "pending"} for remote in body["selected"]}
            if old is not None:
                if set(old["jobs"]) != set(pending) or any(not isinstance(entry, dict) or entry.get("client") != pending[remote]["client"] for remote, entry in old["jobs"].items()):
                    raise IntakeError("Durable intake case association is incomplete; no replacement is assumed.")
                pending = dict(old["jobs"])
            def save_group(state):
                jobs._write(proof, {"version": 1, "type": "group", "digest": preview["digest"], "state": state, "retired": False, "jobs": pending})
            if old is None:
                save_group("reserving")
            for remote in body["selected"]:
                case = body["mapping"][remote]
                self._authority(email, {remote: case})
                key = digest({"operation": operation, "remote": remote})
                submitted = hashlib.sha256(json.dumps(["drive_intake", case, key], separators=(",", ":")).encode()).hexdigest()
                reserved = durable_record(self.scope.queue / ("operation-" + submitted + ".json"))
                if reserved is not None and (reserved.get("kind") != "drive_intake" or reserved.get("client") != case or not isinstance(reserved.get("id"), str) or not re.fullmatch(r"[0-9a-f]{64}", str(reserved.get("payload") or ""))):
                    raise IntakeError("Durable job reservation is incomplete; no replacement is assumed.")
                # Minimize persisted metadata to this case's selected files.
                case_body = {**body, "selected": [remote], "mapping": {remote: case},
                             "clients": [c for c in body["clients"] if c["remote"] == remote]}
                case_preview = {**preview, "snapshot": case_body, "digest": digest(case_body)}
                job = jobs.submit(self.scope.queue, "drive_intake", client=case, by=actor.get("name") or email,
                                  args={"preview": case_preview, "actor": email, "remote": remote, "operation": operation}, operation_id=key)
                pending[remote] = {"client": case, "id": job["id"], "state": job["state"]}
                save_group("reserving")
            save_group("reserved")
        return pending

    def execute(self, job: dict, processor: Callable, progress: Callable | None = None):
        args = job.get("args") or {}
        email, remote = args.get("actor"), args.get("remote")
        body = self._validated_preview(email, args.get("preview"))
        if remote not in body["selected"] or body["mapping"][remote] != job.get("client") or not re.fullmatch(r"[0-9a-f]{64}", str(args.get("operation") or "")):
            raise IntakeError("The job does not match its selected client and operation.")
        actor = self._authority(email, body["mapping"])
        case = body["mapping"][remote]
        phase_path = self.scope.queue / "drive-receipts" / (args["operation"] + "-" + hashlib.sha256(remote.encode()).hexdigest() + "-phase.json")
        sync._plain(phase_path)
        progress = progress or (lambda *_: None)
        with jobs.case_lock(self.scope.queue, case):
            # Recheck after waiting for the reader, including before returning
            # an existing completed phase. Saved snapshots grant no authority.
            self._validated_preview(email, args["preview"])
            self._authority(email, {remote: case})
            saved = durable_record(phase_path)
            if saved is not None and (saved.get("version") != 1 or saved.get("type") != "phase" or saved.get("client") != case or saved.get("remote_digest") != hashlib.sha256(remote.encode()).hexdigest() or not isinstance(saved.get("copy"), dict) or not isinstance(saved.get("processing"), dict) or not isinstance(saved.get("digest"), str)):
                raise IntakeError("Durable intake phase is incomplete; no replacement is assumed.")
            if saved is not None and (saved["copy"].get("state") not in ("running", "completed") or saved["processing"].get("state") not in ("not started", "running", "completed", "held", "failed")):
                raise IntakeError("Durable intake phase has an unsupported state.")
            if saved is not None and saved.get("digest") != args["preview"]["digest"]:
                raise IntakeError("Durable intake phase does not match this job.")
            if saved and saved.get("processing", {}).get("state") in ("completed", "held"):
                outcome = saved["processing"].get("result")
                _, retained = self.scope.case(case)
                if saved.get("copy", {}).get("state") != "completed" or not isinstance(outcome, dict) or outcome.get("processed") is not True or saved["copy"].get("retained_sources") != retained_sources(retained):
                    raise IntakeError("Completed intake proof is incomplete or its retained sources changed.")
                return saved
            settings = self._settings()
            progress(1, 3, "Checking selected Drive documents")
            with self._source(settings) as source:
                current, adapter = self._snapshot(source, settings, email, body["selected"], body["mapping"])
                if digest(current) != args["preview"]["digest"]:
                    raise IntakeError("The source changed since preview; no new documents were copied. Preview again.")
                if saved and saved.get("copy", {}).get("state") == "completed":
                    report = saved["copy"]["report"]
                    _, retained = self.scope.case(case)
                    if saved["copy"].get("retained_sources") != retained_sources(retained):
                        raise IntakeError("The retained source files changed; start a new reviewed intake attempt.")
                else:
                    jobs._write(phase_path, {"version": 1, "type": "phase", "client": case, "remote_digest": hashlib.sha256(remote.encode()).hexdigest(), "digest": args["preview"]["digest"], "copy": {"state": "running"}, "processing": {"state": "not started"}})
                    progress(2, 3, "Copying selected documents into this case")
                    self._validated_preview(email, args["preview"])
                    self._authority(email, {remote: case})
                    with events.acting(actor.get("name") or email, actor["role"], via="staff"):
                        report = sync.mirror(adapter, self.scope.documents, only=[remote], home=self.scope.data, local_ids={remote: case})
            _, retained = self.scope.case(case)
            result = {"version": 1, "type": "phase", "client": case, "remote_digest": hashlib.sha256(remote.encode()).hexdigest(), "digest": args["preview"]["digest"], "copy": {"state": "completed", "report": report,
                      "retained_sources": retained_sources(retained)}, "processing": {"state": "running"}}
            jobs._write(phase_path, result)
            try:
                self._validated_preview(email, args["preview"])
                self._authority(email, {remote: case})
                _, source_folder = self.scope.case(case)
                progress(3, 3, "Processing the copied documents")
                outcome = processor(self.scope, case, source_folder, progress)
                if not isinstance(outcome, dict) or outcome.get("processed") is not True:
                    raise ProcessingFailed("Local processing did not complete; retained source documents remain available.")
                result["processing"] = {"state": "held" if outcome.get("held") else "completed", "result": outcome}
                jobs._write(phase_path, result)
                return result
            except Exception as exc:
                result["processing"] = {"state": "failed", "error": type(exc).__name__, "copied_sources_retained": True}
                jobs._write(phase_path, result)
                raise ProcessingFailed("Drive copying completed; local processing failed. See this installation's intake phase report.") from None


def _purge_job_staging(queue: Path, cases: set[str]) -> tuple[int, int]:
    """Exact generic queue temp names, associated by record or intact proof."""
    sync._plain(queue)
    if not queue.is_dir():
        return 0, 0
    for path in (queue / "done", queue / "submit.lock"):
        sync._plain(path)
    removed, unresolved = 0, 0
    job_name = re.compile(r"\d{20}-drive_intake-[0-9a-f]{8}\.json")
    proof_name = re.compile(r"operation-[0-9a-f]{64}\.json")
    temp_name = re.compile(r"(.+\.json)\.\d+\.\d+\.tmp")
    with oslock.locked(queue / "submit.lock", timeout=10):
        owners = {}
        folders = [queue] + ([queue / "done"] if (queue / "done").is_dir() else [])
        for folder in folders:
            for path in folder.glob("*.json"):
                if not (job_name.fullmatch(path.name) or proof_name.fullmatch(path.name)):
                    continue
                sync._plain(path)
                try:
                    rec = durable_record(path)
                    if rec and rec.get("kind") == "drive_intake" and isinstance(rec.get("client"), str) and CASE_ID.fullmatch(rec["client"]):
                        owners[path.name] = rec["client"]
                        if isinstance(rec.get("id"), str) and job_name.fullmatch(rec["id"] + ".json"):
                            owners[rec["id"] + ".json"] = rec["client"]
                    elif not rec or rec.get("kind") == "drive_intake" or job_name.fullmatch(path.name):
                        unresolved += 1
                except (OSError, ValueError):
                    unresolved += 1
        for folder in folders:
            for path in folder.iterdir():
                match = temp_name.fullmatch(path.name)
                if not match or not (job_name.fullmatch(match[1]) or proof_name.fullmatch(match[1])):
                    continue
                sync._plain(path)
                owner = owners.get(match[1])
                try:
                    rec = durable_record(path)
                except (OSError, ValueError):
                    rec = None
                if rec is not None:
                    if rec.get("kind") != "drive_intake":
                        if owner is not None:
                            unresolved += 1
                        continue
                    client = rec.get("client")
                    if not isinstance(client, str) or not CASE_ID.fullmatch(client) or (owner is not None and owner != client):
                        unresolved += 1
                        continue
                    owner = client
                if owner in cases:
                    path.unlink()
                    removed += 1
                elif owner is None:
                    unresolved += 1
    return removed, unresolved


def purge_receipts(queue: Path, cases: set[str]) -> tuple[int, int]:
    """Called under Q1's case lock; preserve every other case's retry proof.

    Only exact producer filenames and proven case associations are removed.
    Locks stay in place: unlinking an OS lock can split concurrent writers.
    Returns removed/rewritten records and unresolved records, never names.
    """
    removed, unresolved = _purge_job_staging(Path(queue), cases)
    folder = Path(queue) / "drive-receipts"
    sync._plain(folder)
    if not folder.exists():
        return removed, unresolved
    if not folder.is_dir():
        raise IntakeError("Drive receipt store is unavailable.")
    group_name = re.compile(r"([0-9a-f]{64})\.json(?:\.\d+\.\d+\.tmp)?")
    phase_name = re.compile(r"([0-9a-f]{64})-([0-9a-f]{64})-phase\.json(?:\.\d+\.\d+\.tmp)?")
    groups = {}
    for path in folder.iterdir():
        sync._plain(path)
        match = group_name.fullmatch(path.name) or phase_name.fullmatch(path.name)
        if match and path.is_file():
            groups.setdefault(match[1], []).append(path)
        elif not re.fullmatch(r"[0-9a-f]{64}\.lock", path.name):
            unresolved += 1
    for operation, paths in groups.items():
        lock = folder / (operation + ".lock")
        sync._plain(lock)
        with oslock.locked(lock, timeout=10):
            associations = {}
            group = folder / (operation + ".json")
            # Capture bindings before removing members; they also identify an
            # interrupted phase whose JSON bytes never finished writing.
            try:
                saved = durable_record(group)
                if saved is not None:
                    if saved.get("version") != 1 or saved.get("type") != "group" or not isinstance(saved.get("jobs"), dict):
                        raise IntakeError("Unassignable Drive group")
                    for remote, member in saved["jobs"].items():
                        if not isinstance(remote, str) or not REMOTE_ID.fullmatch(remote) or not isinstance(member, dict) or not isinstance(member.get("client"), str) or not CASE_ID.fullmatch(member["client"]):
                            raise IntakeError("Unassignable Drive member")
                        associations[hashlib.sha256(remote.encode()).hexdigest()] = member["client"]
            except (OSError, ValueError, TypeError):
                saved = None
                associations = {}
            for path in paths:
                if not path.exists():
                    continue
                sync._plain(path)
                phase = phase_name.fullmatch(path.name)
                parsed = False
                try:
                    rec = durable_record(path)
                    parsed = True
                    if phase:
                        bound = associations.get(phase[2])
                        if rec is None or rec.get("version") != 1 or rec.get("type") != "phase" or not isinstance(rec.get("client"), str) or not CASE_ID.fullmatch(rec["client"]) or rec.get("remote_digest") != phase[2] or (bound is not None and bound != rec["client"]):
                            raise IntakeError("Unassignable Drive phase")
                        if rec["client"] in cases:
                            path.unlink()
                            removed += 1
                    else:
                        if rec is None or rec.get("version") != 1 or rec.get("type") != "group" or not isinstance(rec.get("jobs"), dict):
                            raise IntakeError("Unassignable Drive group")
                        members = rec["jobs"]
                        if any(not isinstance(remote, str) or not REMOTE_ID.fullmatch(remote) or not isinstance(v, dict) or not isinstance(v.get("client"), str) or not CASE_ID.fullmatch(v["client"]) for remote, v in members.items()):
                            raise IntakeError("Unassignable Drive member")
                        target = [remote for remote, v in members.items() if v["client"] in cases]
                        if target:
                            if path.suffix == ".tmp":
                                # No writer holds the group lock; this is an
                                # abandoned write, not another case's proof.
                                path.unlink()
                            else:
                                rec["jobs"] = {remote: v for remote, v in members.items() if remote not in target}
                                rec["retired"] = True
                                jobs._write(path, rec)
                            removed += 1
                except (OSError, ValueError, TypeError):
                    # The exact phase filename plus intact group association
                    # proves ownership even when interrupted JSON is damaged.
                    if phase and not parsed and associations.get(phase[2]) in cases:
                        path.unlink()
                        removed += 1
                    else:
                        unresolved += 1
    return removed, unresolved


def process_local(scope: FirmScope, case: str, source: Path, progress: Callable, *, names: list[str],
                  use_policies: bool = True, use_vision: bool = True):
    """Canonical incremental processing, preserving existing portal/raw evidence.

    Executed only by the installed job worker. Source association was checked
    before copying; no portal engine, notifier, publish or automatic signoff.
    """
    _, expected = scope.case(case)
    if source != expected:
        raise IntakeError("The processor source is not the selected case's complete source folder.")
    # Explicit roots may not inherit another installation's index/query paths.
    for name, expected_path in {"I485_INDEX": scope.data / "index.db", "I485_QUERY_DB": scope.data / "query.db",
                                "I485_CASES": scope.cases, "I485_CLIENTS_ROOT": scope.documents, "I485_JOBS": scope.queue}.items():
        if os.environ.get(name) and Path(os.environ[name]).absolute() != expected_path:
            raise IntakeError("The worker inherits another installation's processing paths.")
    import schema_path
    schemas = schema_path.schemas_in(scope.root)
    sync._plain(schemas)
    if schema_path.ROOT.absolute() != schemas or not schemas.is_dir():
        raise IntakeError("The effective schemas/firm profile do not belong to this installation.")
    if not names or len(set(names)) != len(names) or any(not isinstance(n, str) or Path(n).name != n or not n.endswith(".pdf") for n in names):
        raise IntakeError("The processor requires explicit selected retained PDF names.")
    from classify import extract_pages, split_documents
    from batch import split_pages
    import inbox
    rows, paged, split_info = [], {}, {}
    for name in names:
        path = source / name
        sync._plain(path)
        pages = extract_pages(path)
        if not pages:
            raise ProcessingFailed("A selected source has no readable pages.")
        from extract.sij_order import form_block
        pages[-1] += form_block(path)
        documents, by_page = split_pages(name, pages, split_documents(pages))
        rows.extend(documents)
        paged.update(by_page)
        split_info[name] = {"pages": len(pages)}
    with jobs.case_lock(scope.queue, case):
        scope.case(case)
        # Existing derivation settings are preserved; a worker flag cannot
        # silently turn off an existing case's approved policy/EV holds.
        out = inbox.reprocess_documents(scope.cases / case, source, rows, split_info, db_path=scope.data / "index.db", source="drive", pages=paged)
    if out.get("refill_error") or not out.get("refilled"):
        return {"processed": False, "review_required": True, "refill_error": out.get("refill_error")}
    import document_instances, subject_attribution, critical_review
    held = document_instances.problems(scope.cases / case) + subject_attribution.problems(scope.cases / case) + critical_review.problems(scope.cases / case)
    return {"processed": True, "held": bool(held), "review_required": True,
            "signed": False, "sent": False}


def installed(scope: FirmScope, *, transport=None) -> SelectedIntake:
    """Production composition; caller has already selected its trusted root.

    Configuration is installation-owned, not a request or durable role snapshot.
    This factory does not connect to Google until preview/execution is invoked.
    """
    from review.auth import Accounts
    import restricted
    from .gdrive import GoogleDrive

    def settings_reader():
        from .drive_settings import read
        return read(scope)

    def actor_reader(email):
        return next((u for u in Accounts(scope.data / "review_users.json").users() if u["email"] == email), None)

    def factory(settings, *, write):
        if write:
            raise IntakeError("Selected intake does not write to Google Drive.")
        return GoogleDrive(settings, env={}, transport=transport, write=False, data_root=scope.data)

    return SelectedIntake(scope, settings_reader, factory, actor_reader, restricted.visible_to)


def worker_handler(ctx, job, progress):
    """Small jobs registry seam, ready for queue owner's separate patch review."""
    root = Path(ctx.clients).absolute().parent.parent
    scope = FirmScope(root)
    if Path(ctx.clients).absolute() != scope.cases or Path(ctx.root).absolute() != scope.queue or (ctx.portal is not None and Path(ctx.portal).absolute() != scope.portal):
        raise IntakeError("The worker does not match the selected intake's configured installation.")
    def processor(current_scope, case, source, emit):
        from .base import RemoteDoc
        remote = job["args"]["remote"]
        selected = next(c for c in job["args"]["preview"]["snapshot"]["clients"] if c["remote"] == remote)
        names = [sync._filename(RemoteDoc(d["id"], d["name"], d["mime"])) for d in selected["documents"] if d["readable"]]
        return process_local(current_scope, case, source, emit, names=names, use_policies=ctx.use_policies)
    return installed(scope).execute(job, processor, progress)
