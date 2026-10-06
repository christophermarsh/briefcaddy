"""Versioned independent references and bounded source validation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
HASH = re.compile(r"[0-9a-f]{64}")
LABELS = {"supported", "unsupported", "unknown", "disputed"}
PARTITIONS = {"calibration", "evaluation"}
MAX_SOURCE = 64 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON number")))


def identifier(value, title):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(title + " requires an opaque lower-case id")
    return value


def safe_source(root, relative):
    """Reject lexical/resolved escape and every symlink/reparse ancestor."""
    root = Path(root).absolute()
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("source path must be corpus-relative")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts) or Path(relative).is_absolute():
        raise ValueError("source path escapes corpus")
    target = root.joinpath(*parts)
    for ancestor in (root, *root.parents, *target.parents, target):
        if ancestor.is_symlink() or (ancestor.exists() and getattr(ancestor.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("source symlink/reparse path refused")
    if not target.resolve(strict=True).is_relative_to(root.resolve(strict=True)) or not target.is_file():
        raise ValueError("source path escapes corpus or is not a file")
    if target.stat().st_size > MAX_SOURCE:
        raise ValueError("source exceeds 64 MiB evaluation limit")
    return target


def fact_id(case, fact):
    return (case, fact["subject"], fact["key"], fact.get("instance") or "")


def independently_scored(fact):
    a = fact.get("adjudication") or {}
    return fact["label"] in ("supported", "unsupported") and a.get("basis") == "independent" and bool(a.get("by")) and bool(a.get("history"))


def source_page_count(path):
    """Validate original bounds from source bytes, never from reader claims."""
    if path.suffix.lower() == ".txt":
        return len(path.read_text(encoding="utf-8").split("\f"))
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted or not reader.pages:
            raise ValueError("source bounds require unencrypted nonempty PDF")
        return len(reader.pages)
    raise ValueError("page bounds supported only for UTF-8 text and PDF sources")


def adjudication(value):
    if not isinstance(value, dict) or value.get("basis") not in ("independent", "workflow", "unlabeled") or not isinstance(value.get("history"), list):
        raise ValueError("adjudication basis/history required")
    if value["basis"] == "independent" and (not isinstance(value.get("by"), str) or not value["by"].strip() or not value["history"]):
        raise ValueError("independent references require named adjudicator and history")
    if any(not isinstance(entry, dict) or not isinstance(entry.get("reason"), str) or not entry["reason"].strip() for entry in value["history"]):
        raise ValueError("adjudication history requires reasoned entries")
    return value


def validate(manifest, root):
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("corpus version must be 1")
    identifier(manifest.get("id"), "corpus")
    if not isinstance(manifest.get("synthetic_only"), bool):
        raise ValueError("declare synthetic_only explicitly")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")
    case_ids, groups, hashes, facts = set(), {}, {}, set()
    docs = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case must be an object")
        cid = identifier(case.get("id"), "case")
        if cid in case_ids:
            raise ValueError("duplicate case")
        case_ids.add(cid)
        partition = case.get("partition")
        if partition not in PARTITIONS:
            raise ValueError("partition must be calibration or evaluation")
        group = identifier(case.get("family_group"), "family group")
        if group in groups and groups[group] != partition:
            raise ValueError("family/case partition leakage")
        groups[group] = partition
        subjects = case.get("subjects")
        if not isinstance(subjects, list) or any(not isinstance(s, str) for s in subjects) or len(set(subjects)) != len(subjects):
            raise ValueError("unique subject ids required")
        for subject in subjects:
            identifier(subject, "subject")
        if case.get("completion") not in ("accepted", "failed", "held", "unfinished"):
            raise ValueError("case completion state required; failures remain in cohort")
        if not isinstance(case.get("documents"), list) or not isinstance(case.get("expected"), list):
            raise ValueError("documents and expected facts must be lists")
        local_docs = set()
        instances = set()
        for doc in case["documents"]:
            if not isinstance(doc, dict):
                raise ValueError("document must be an object")
            did = identifier(doc.get("id"), "document")
            if did in local_docs:
                raise ValueError("duplicate document")
            local_docs.add(did)
            path = safe_source(root, doc.get("path"))
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if doc.get("sha256") != actual:
                raise ValueError("source bytes/hash changed")
            if actual in hashes and hashes[actual] != partition:
                raise ValueError("duplicate source hash partition leakage")
            hashes[actual] = partition
            for field in ("language", "quality", "type"):
                if not isinstance(doc.get(field), str) or not doc[field]:
                    raise ValueError("document language/quality/type strata required")
            if doc.get("subject") is not None and doc["subject"] not in subjects:
                raise ValueError("document subject not in case")
            if doc.get("page_count") is not None and (type(doc["page_count"]) is not int or doc["page_count"] < 1):
                raise ValueError("page count must be a positive integer")
            if doc.get("page_count") is not None and doc["page_count"] != source_page_count(path):
                raise ValueError("declared page count differs from original source bytes")
            if doc.get("subject_scope") not in (None, "multi_subject", "unknown", "validated_single_subject"):
                raise ValueError("unknown document subject scope")
            if doc.get("subject_scope") == "validated_single_subject":
                a = adjudication(doc.get("subject_adjudication"))
                if a.get("basis") != "independent" or not doc.get("subject"):
                    raise ValueError("single-subject scope requires independent named adjudication")
            if not isinstance(doc.get("attributions", []), list) or not isinstance(doc.get("instances", []), list):
                raise ValueError("attributions and instances must be lists")
            for attribution in doc.get("attributions") or []:
                if not isinstance(attribution, dict) or attribution.get("subject") not in subjects or not attribution.get("key") or not isinstance(attribution.get("adjudication"), dict):
                    raise ValueError("fact/source attribution requires subject/key/adjudication")
                if "page" in attribution and (type(attribution["page"]) is not int or attribution["page"] < 0 or doc.get("page_count") is not None and attribution["page"] >= doc["page_count"]):
                    raise ValueError("attribution page outside original source")
                adjudication(attribution["adjudication"])
            for instance in doc.get("instances") or []:
                if not isinstance(instance, dict):
                    raise ValueError("instance must be an object")
                iid = instance.get("id")
                if not isinstance(iid, str) or not HASH.fullmatch(iid) or iid in instances:
                    raise ValueError("unique full evidence instance digest required")
                bounds = instance.get("pages")
                if not isinstance(bounds, list) or len(bounds) != 2 or any(type(n) is not int or n < 0 for n in bounds) or bounds[0] > bounds[1]:
                    raise ValueError("instance pages must be inclusive original 0-based range")
                if doc.get("page_count") is None or bounds[1] >= doc["page_count"]:
                    raise ValueError("instance range requires validated original source bounds")
                instances.add(iid)
            docs[(cid, did)] = doc
        for fact in case["expected"]:
            if not isinstance(fact, dict):
                raise ValueError("expected fact must be an object")
            if fact.get("subject") not in subjects or not isinstance(fact.get("key"), str) or not fact["key"]:
                raise ValueError("fact subject/key required")
            if fact.get("instance") is not None and fact["instance"] not in instances:
                raise ValueError("fact references unknown instance")
            if not isinstance(fact.get("documents", []), list) or any(did not in local_docs for did in fact.get("documents") or []):
                raise ValueError("expected fact names unknown document")
            fid = fact_id(cid, fact)
            if fid in facts:
                raise ValueError("duplicate independent fact (printed boxes are not facts)")
            facts.add(fid)
            if fact.get("label") not in LABELS:
                raise ValueError("supported/unsupported/unknown/disputed reference required")
            if fact["label"] == "supported" and "value" not in fact:
                raise ValueError("supported reference requires value")
            adjudication(fact.get("adjudication"))
            if fact.get("safe_hold") is not None and type(fact["safe_hold"]) is not bool:
                raise ValueError("safe hold must be an independently adjudicated boolean")
        if not isinstance(case.get("pdf_boxes", []), list):
            raise ValueError("PDF references must be a list")
        box_ids = set()
        for box in case.get("pdf_boxes", []):
            if not isinstance(box, dict) or not isinstance(box.get("id"), str) or not box["id"] or box["id"] in box_ids or box.get("subject") not in subjects or not isinstance(box.get("key"), str) or not box["key"] or "value" not in box:
                raise ValueError("unique PDF reference id/subject/key/value required")
            if box.get("instance") is not None and box["instance"] not in instances:
                raise ValueError("PDF reference names unknown instance")
            adjudication(box.get("adjudication")); box_ids.add(box["id"])
    return {"manifest_digest": digest(manifest), "cases": len(cases), "documents": len(docs), "facts": len(facts), "partitions": sorted(set(groups.values()))}


def write_new(path, value):
    """Never overwrite a frozen corpus/run/policy artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, indent=2, ensure_ascii=False, allow_nan=False)
        out.write("\n")
