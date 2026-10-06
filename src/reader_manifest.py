"""Declared reader configuration captured at read time, never legacy backfill.

The field extractor knows its input text and own code. Upstream OCR/model
assets are not yet pinned or returned by those readers. Their descriptors
are explicit, and are NOT a claim about immutable deployed model weights.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from document_instances import digest
import schema_path

ROOT = Path(__file__).resolve().parents[1]
VERSION = 1
_HASHES = {}


def _hash(path: Path):
    if not path.is_file():
        return None
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    if path not in _HASHES or _HASHES[path][0] != stamp:
        _HASHES[path] = stamp, hashlib.sha256(path.read_bytes()).hexdigest()
    return _HASHES[path][1]


def current(doc_type: str) -> dict:
    import read_scope
    return read_scope.once(("reader-manifest", doc_type), lambda: _current(doc_type), copy=True)


def _current(doc_type: str) -> dict:
    from extract import EXTRACTORS
    reader = EXTRACTORS.get(doc_type)
    files = {"src/document_instances.py", "src/subject_attribution.py", "src/reader_manifest.py", "src/schema_path.py", "src/read_scope.py"}
    for folder in ("src/extract", "src/classify"):
        files.update(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / folder).rglob("*.py"))
    assets = [schema_path.path("law", "br_municipalities"), schema_path.path("register", "document_types"),
              schema_path.path("register", "reader_formats")]
    assets += schema_path.glob("geo")
    if reader:
        files.add("src/" + reader.__module__.replace(".", "/") + ".py")
    if doc_type == "i360_approval":
        files.update({"src/extract/i360_approval.py", "src/extract/uscis_notice.py"})
    if doc_type == "us_passport":
        files.add("src/extract/passport.py")
    if doc_type == "intake_questionnaire":
        for folder in ("src/questionnaire", "src/vision"):
            files.update(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / folder).rglob("*")
                         if p.is_file() and p.suffix in {".py", ".json"})
        files.add("src/portal/bank.py")
        assets += schema_path.glob("paper_map") + [schema_path.path("question", "intake")]
    versions = {}
    for package in ("pypdf", "Pillow", "numpy", "pytesseract"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    endpoint = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    url = urlsplit(endpoint)
    endpoint_label = urlunsplit((url.scheme, (url.hostname or "") + (":" + str(url.port) if url.port else ""), url.path, "", ""))
    return {"version": VERSION, "doc_type": doc_type, "capture_scope": "field_extraction_from_supplied_text",
            "source_files": {f: _hash(ROOT / f) for f in sorted(files)},
            "normalization_assets": {str(p.relative_to(schema_path.ROOT)).replace("\\", "/"): _hash(p) for p in sorted(set(assets))},
            "python": platform.python_version(), "packages": versions,
            "declared_reader_release_revision": os.environ.get("READER_RELEASE_REVISION") or "unversioned",
            "upstream": {"execution_identity": "unavailable", "ocr_asset_sha256": None, "model_weight_sha256": None,
                         "descriptors": {"tesseract_command": os.environ.get("TESSERACT_CMD") or "auto-discovery",
                                         "vision_endpoint": endpoint_label, "vision_endpoint_config_digest": digest(endpoint),
                                         "vision_model_tag": os.environ.get("VISION_MODEL", "qwen3-vl:8b-instruct"),
                                         "vision_options": {"temperature": 0, "num_ctx": 8192, "num_predict": 400}},
                         "release_gate": "Actual OCR/model asset identity requires deployment verification; descriptors are not weight hashes."}}


def fingerprint(manifest: dict | None) -> str | None:
    return digest(manifest) if manifest is not None else None
