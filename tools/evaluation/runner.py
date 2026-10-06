"""Local text-layer product adapter; independent references never enter readers."""
from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from .corpus import digest, safe_source, validate, write_new

REPO = Path(__file__).resolve().parents[2]


def configuration(supplied):
    allowed = {"version", "adapter", "subject_map", "instance_fact_keys"}
    if not isinstance(supplied, dict) or set(supplied) - allowed or supplied.get("version") != 1 or supplied.get("adapter") != "pipeline_text":
        raise ValueError("only version-1 pipeline_text configuration is supported")
    subjects = supplied.get("subject_map") or {}
    if not isinstance(subjects, dict) or any(not isinstance(roles, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in roles.items()) for roles in subjects.values()):
        raise ValueError("subject_map must map case/reader role to opaque subject")
    instance_keys = supplied.get("instance_fact_keys", [])
    if not isinstance(instance_keys, list) or any(not isinstance(k, str) or not k for k in instance_keys) or len(set(instance_keys)) != len(instance_keys):
        raise ValueError("instance_fact_keys must be unique semantic fact keys")
    return {"version": 1, "adapter": "pipeline_text", "adapter_version": 1, "subject_map": subjects, "instance_fact_keys": instance_keys,
            "normalization_version": "bound-to-actual-implementation-digest", "ocr": False, "vision": False,
            "translation": False, "rules": False, "policies": False, "questionnaire_reader": False, "network": "blocked-in-process",
            "reader_scope": "existing batch.process_documents over UTF-8 text or PDF text layers; no full packet/PDF filling, OCR, translation or human review"}


def implementation():
    files = {}
    for folder, suffix in (("src", "*.py"), ("schemas", "*.json"), ("tools/evaluation", "*.py")):
        for path in sorted((REPO / folder).rglob(suffix)):
            if path.is_symlink():
                raise ValueError("implementation symlink refused")
            files[path.relative_to(REPO).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("tools/evaluate_corpus.py", "requirements.txt", "requirements.lock"):
        path = REPO / name
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    git_dir = REPO / ".git"
    if git_dir.is_file():
        pointer = git_dir.read_text(encoding="utf-8").strip().removeprefix("gitdir: ").replace("\\", "/")
        if os.name != "nt" and len(pointer) > 2 and pointer[1:3] == ":/":
            pointer = "/mnt/" + pointer[0].lower() + pointer[2:]
        git_dir = Path(pointer)
        if not git_dir.is_absolute():
            git_dir = REPO / git_dir
    git = ["git", "--git-dir=" + str(git_dir), "--work-tree=" + str(REPO)]
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    status = subprocess.run([*git, "status", "--porcelain", "--untracked-files=all", "--", "src", "schemas", "tools/evaluation", "tools/evaluate_corpus.py", "requirements.txt", "requirements.lock"], capture_output=True, text=True, check=True).stdout.splitlines()
    return {"commit": head, "tracked_tree_dirty": bool(status), "status_scope": "source/schema/evaluation/configuration includes staged, unstaged and untracked", "status": status, "inspection_succeeded": True, "files": files, "digest": digest(files)}


def _pages(path, data):
    if path.suffix.lower() == ".txt":
        return data.decode("utf-8").split("\f")
    if path.suffix.lower() == ".pdf":
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or not reader.pages:
            raise ValueError("adapter requires unencrypted PDF with pages")
        return [page.extract_text() or "" for page in reader.pages]
    raise ValueError("pipeline_text adapter supports .txt and .pdf text layers only")


def run(manifest, corpus_root, supplied, output, command):
    validate(manifest, corpus_root)
    config = configuration(supplied)
    # Cached product modules can hold storage/config paths chosen before this
    # adapter's isolation. Refuse instead of quietly claiming fresh isolation.
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if filename and name != "schema_path" and Path(filename).resolve().is_relative_to((REPO / "src").resolve()):
            raise ValueError("product modules already imported; run adapter CLI in a fresh isolated process")
    output = Path(output)
    if output.exists():
        raise FileExistsError("run output must be a new isolated directory")
    # Validate role aliases before executing; these only translate product role
    # names into corpus subject ids, never tell the reader document ownership.
    for case in manifest["cases"]:
        mapping = config["subject_map"].get(case["id"], {})
        if any(value not in case["subjects"] for value in mapping.values()):
            raise ValueError("reader subject alias not in corpus case")
    if set(config["subject_map"]) - {case["id"] for case in manifest["cases"]}:
        raise ValueError("subject map names unknown case")
    info = implementation()
    config["normalization_implementation_digest"] = info["digest"]
    output.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()
    began = time.monotonic()
    facts, outcomes, classifications, passed = [], [], [], {}
    # Pure text batch requires no service. Blocking sockets here changes only
    # this local run process, never the user's host/network configuration.
    def no_network(*args, **kwargs):
        raise RuntimeError("evaluation adapter does not permit network")
    with tempfile.TemporaryDirectory(prefix="evaluation-", dir=output) as scratch, patch("socket.socket.connect", no_network), patch("socket.create_connection", no_network):
        stores = {"I485_SETTINGS": "settings.json", "I485_MAINTENANCE_LOG": "maintenance_log.json", "I485_DEPLOYMENT": "deployment.json", "I485_LIVE_STATUS": "maintenance_status.json", "I485_RULES_APPROVED": "rules_approved.json", "I485_POLICIES_FIRM": "policies_firm.json", "I485_INDEX": "index.db", "I485_EVENTS": "events.jsonl", "I485_QUERY_DB": "query.db", "I485_JOBS": "jobs", "I485_ROSTER": "roster.json", "I485_BACKUP_LOG": "backup_log.json", "I485_POSTURE": "posture.json", "I485_INBOX": "inbox", "I485_CASES": "clients", "I485_ACCURACY_HISTORY": "accuracy_history.jsonl", "I485_REFERENCE": "references", "I485_AUDIT_FILL": "audit_fill.json", "I485_READER_EXAMPLES": "examples", "PORTAL_DATA": "portal"}
        controls = {"I485_SHADOW": "0", "I485_LIVE_CHECKS": "0", "I485_QUERY_REFRESH": "0", "I485_FEEDBACK_EVERY": "0", "I485_JOBS_WORKER": "0", "I485_POSTURE_CHECKS": "0", "I485_ACCURACY": "0", "I485_AUDIT": "0", "I485_LEARNING": "0", "I485_FIND_EMBEDDER": "hashing", "PORTAL_READ_AT_ONCE": "0", "PORTAL_OUTBOX_FULL_LINKS": "0", "OLLAMA_URL": "http://127.0.0.1:1"}
        product_prefixes = ("I485_", "PORTAL_", "OLLAMA_", "HF_", "HUGGINGFACE_", "TRANSFORMERS_")
        isolation = {key: value for key, value in os.environ.items() if not key.startswith(product_prefixes) and key != "TESSERACT_CMD"}
        isolation.update({key: str(Path(scratch) / filename) for key, filename in stores.items()}); isolation.update(controls)
        (Path(scratch) / "clients").mkdir()
        config["isolation"] = {"stores": stores, "controls": controls, "inherited_product_controls": False, "cached_product_modules": "refused"}
        with patch.dict(os.environ, isolation, clear=True):
            return _execute(manifest, corpus_root, output, command, config, info, started, began, scratch, facts, outcomes, classifications, passed)


def _execute(manifest, corpus_root, output, command, config, info, started, began, scratch, facts, outcomes, classifications, passed):
    import batch
    from classify import split_documents
    parameters = inspect.signature(batch.process_documents).parameters
    for case in manifest["cases"]:
        if case["partition"] != "evaluation":
            continue
        cid = case["id"]
        documents, paged, aliases, retained = [], {}, {}, {}
        errors = []
        for doc in case["documents"]:
            try:
                path = safe_source(corpus_root, doc["path"])
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != doc["sha256"]:
                    raise ValueError("source changed after validation")
                pages = _pages(path, data)
                name = doc["id"] + path.suffix.lower()
                own = Path(scratch) / cid / name; own.parent.mkdir(parents=True, exist_ok=True); own.write_bytes(data)
                retained[name] = {"document": doc["id"], "sha256": doc["sha256"], "page_count": len(pages)}
                split, source_pages = batch.split_pages(name, pages, split_documents(pages))
                documents.extend(split); paged.update(source_pages)
                for alias, _text in split:
                    aliases[alias] = doc["id"]
            except Exception as exc:  # retain malformed/unsupported source failures without raw content
                errors.append({"document": doc["id"], "error_type": type(exc).__name__})
        kwargs = {"rules": None, "policies": None, "firm_profile": None, "questionnaire_reader": None, "answers": None, "office_answers": None, "pages": paged}
        boundary_context = None
        if "boundary_context" in parameters:
            import document_instances
            boundary_context = document_instances.context(Path(scratch) / cid, None, list(retained))
            if boundary_context.get("hashes") != {name: source["sha256"] for name, source in retained.items()}:
                raise ValueError("retained source changed before product boundary context")
            kwargs["boundary_context"] = boundary_context
        passed[cid] = {"parameters": sorted(kwargs), "documents": [name for name, _ in documents],
                       "boundary_context_digest": digest(boundary_context) if boundary_context is not None else None,
                       "boundary_context_supplied": boundary_context is not None,
                       "retained_sources": retained, "reference_labels_passed": False}
        try:
            result = batch.process_documents(cid, documents, **kwargs)
            boundary_plans = getattr(result, "boundary_plans", {})
            instance_map = {}
            for name, plan in boundary_plans.items():
                original = retained.get(name)
                if original is None or plan.get("source_sha256") != original["sha256"]:
                    raise ValueError("product boundary plan lacks matching retained source identity")
                for part in plan.get("instances", []):
                    for alias in part.get("doc_ids", []):
                        aliases[alias] = original["document"]
                        instance_map[alias] = part
            passed[cid]["boundary_plans"] = boundary_plans
            graph = result.raw_graph or result.graph
            for key, fact in sorted(graph.all_facts().items()):
                role = key.split(".", 1)[0]
                subject = config["subject_map"].get(cid, {}).get(role, "unknown")
                state = "filled" if fact.status == "resolved" and fact.value is not None else "held" if fact.status == "conflict" else "abstained"
                hold = getattr(fact, "hold", None) or getattr(fact, "held", None)
                if hold:
                    state = "held"
                proposal = {"state": state}
                if state == "filled":
                    proposal["value"] = fact.value
                evidence = []
                instances = set()
                for source in fact.sources:
                    if source.doc_id not in aliases:
                        continue
                    link = {"document": aliases[source.doc_id], "reader_doc_id": source.doc_id, "page": source.page, "raw_value": source.raw_value}
                    instance = getattr(source, "instance_id", None)
                    part = instance_map.get(source.doc_id)
                    if part:
                        instance = instance or part["instance_id"]
                        link["pages_zero_based"] = [part["first"], part["last"]]
                        link["retained_source_sha256"] = part["source_sha256"]
                    if instance:
                        link["instance"] = instance; instances.add(instance)
                    evidence.append(link)
                row = {"case": cid, "subject": subject, "key": key, "proposal": proposal, "accepted": None, "evidence": evidence}
                # Evidence links always retain instance identity. Fact identity
                # remains person/key unless the actual adapter configuration
                # explicitly declares a key's instance-specific semantics.
                if key in config["instance_fact_keys"] and len(instances) == 1:
                    row["instance"] = next(iter(instances))
                facts.append(row)
            classifications.append({"case": cid, "documents": {name: item.doc_type for name, item in sorted(result.classifications.items())}})
            outcomes.append({"case": cid, "state": "failed" if errors or result.errors else "completed", "document_errors": errors, "reader_error_count": len(result.errors)})
        except Exception as exc:  # retain a failed case in the cohort; no value/traceback disclosure in report
            outcomes.append({"case": cid, "state": "failed", "error_type": type(exc).__name__, "document_errors": errors})
    observations = {"version": 1, "corpus_digest": digest(manifest), "configuration_digest": digest(config), "facts": facts, "pdf_boxes": [], "labor": [], "outcomes": outcomes, "classifications": classifications}
    runtime = {"python": sys.version, "platform": platform.platform(), "packages": {name: importlib.metadata.version(name) for name in ("pypdf", "Pillow")}}
    provenance = {"version": 1, "kind": "local_pipeline_execution", "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                  "machine_seconds": time.monotonic() - began, "command": command, "configuration": config, "configuration_digest": digest(config),
                  "implementation": info, "runtime": runtime, "corpus_digest": digest(manifest), "observations_digest": digest(observations), "actual_call": passed,
                  "scope": config["reader_scope"], "human_labor_measured": False}
    write_new(output / "observations.json", observations)
    write_new(output / "run.json", provenance)
    return observations, provenance


def imported(observations, manifest, configuration_declared, provenance_declared, command):
    if not isinstance(provenance_declared, dict) or not provenance_declared.get("producer") or not provenance_declared.get("evidence"):
        raise ValueError("import needs declared producer and original evidence provenance")
    if observations.get("configuration_digest") != digest(configuration_declared) or observations.get("corpus_digest") != digest(manifest):
        raise ValueError("imported configuration/corpus digest mismatch")
    return {"version": 1, "kind": "imported_observations", "execution_by_this_tool": False, "configuration_is_declared_only": True,
            "configuration": configuration_declared, "configuration_digest": digest(configuration_declared), "corpus_digest": digest(manifest),
            "observations_digest": digest(observations), "declared_provenance": provenance_declared, "command": command}
