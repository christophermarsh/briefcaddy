"""Document boundaries and immutable evidence identity (EV1).

Original page ranges are zero-based and inclusive. A filename is an alias,
never an approval identity. Readers can inspect a draft while a boundary is
unresolved; that source supplies no facts until a named review settles it.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
import read_scope

VERSION = 1
SEGMENTER_VERSION = "instances-1"
_PAGE = re.compile(r"\bPage\s+(\d+)\s+(?:of|/)\s*(\d+)\b", re.I)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_hash(path: Path) -> str | None:
    return read_scope.once(("original-sha256", str(path.absolute())), lambda: _source_hash(path))


def _source_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def identity(content_hash: str, first: int, last: int) -> str:
    return digest({"identity": "original-range-1", "sha256": content_hash, "pages_zero_based": [first, last]})


def _start(text: str, kind: str) -> bool:
    if kind == "i94":
        return bool(re.search(r"Most\s+Recent\s+I[- ]?94|Admission\s*\(?I[- ]?94\)?\s+Record\s+Number", text, re.I))
    if kind in {"passport", "us_passport"}:
        return bool(re.search(r"P<[A-Z]{3}|Passport\s*(?:No\.?|Number)\s*[:#]", text, re.I))
    if kind.endswith("_approval") or kind in {"uscis_notice", "i485_receipt"}:
        return bool(re.search(r"[I1][- ]?797.{0,30}NOTICE|NOTICE\s+OF\s+ACTION", text, re.I))
    page = _PAGE.search(text)
    return bool(page and int(page[1]) == 1)


def _continued(previous: str, current: str, kind: str) -> bool:
    a, b = _PAGE.search(previous), _PAGE.search(current)
    if a and b and a[2] == b[2] and int(b[1]) == int(a[1]) + 1:
        return True
    # Bare continuation words identify neither the document nor its subject.
    # Require a matching stable record anchor when page sequence is absent.
    a_id, b_id = _anchor(previous, kind), _anchor(current, kind)
    return bool(a_id and a_id == b_id and re.search(r"\b(?:continued|continuation)\b", current[:250], re.I))


def _anchor(text: str, kind: str) -> str | None:
    found = _anchors(text, kind)
    return found[0] if found else None


def _anchors(text: str, kind: str) -> list[str]:
    if kind == "i94":
        pattern = r"(?:Admission\s*\(?)?I[- ]?94\)?\s*(?:Record\s*)?Number\s*[:#]?\s*([A-Z0-9]{9,13})"
    elif kind in {"passport", "us_passport"}:
        pattern = r"Passport\s*(?:No\.?|Number)\s*[:#]?\s*([A-Z0-9]{5,12})"
    else:
        pattern = r"\b((?:EAC|WAC|LIN|SRC|MSC|NBC|IOE)\d{10})\b"
    return [match.upper() for match in re.findall(pattern, text, re.I)]


def analyze(pages: list[str]) -> list[dict[str, Any]]:
    """Conservative page grouping; uncertainty is explicit, never silent."""
    from classify.classifier import _FORM_FOOTER, _FORM_TYPES, classify_text

    if not pages:
        return []
    kinds = [classify_text(p).doc_type for p in pages]
    forms = set(_FORM_TYPES.values())
    result: list[dict[str, Any]] = []
    for number, (text, kind) in enumerate(zip(pages, kinds)):
        previous = result[-1] if result else None
        old_anchor = _anchor("\n".join(pages[previous["first"]:number]), previous["type"]) if previous else None
        new_anchor = _anchor(text, kind)
        translation = bool(re.search(r"\b(?:certification of translation|translator.s certification|translation certificate)\b", text, re.I))
        form_part = bool(previous and previous["type"] in forms and kind not in forms and re.search(r"\bPart\s+\d{1,2}\.", text)
                         and not _FORM_FOOTER.search(text) and not re.search(r"P<[A-Z]{3}|V[A-Z]USA|[I1]-?797", text)
                         and not _start(text, kind))
        weak_association = bool(previous and (translation or form_part))
        same_anchor = bool(old_anchor and new_anchor == old_anchor)
        # Page numbering corroborates the same recognized kind. It cannot
        # join known different kinds or establish an unknown sheet's identity.
        continuation = bool(previous and kind == previous["type"] and
                            _continued(pages[number - 1], text, previous["type"]))
        if weak_association:
            kind = previous["type"]
            continuation = same_anchor  # phrase alone only keeps pages associated for review
        # New independent evidence wins over generic continuation words and
        # page numbering (two unrelated records can both say "Page 2 of 2").
        independent = bool(previous and ((old_anchor and new_anchor and old_anchor != new_anchor)
                           or (_start(text, kind) and not (old_anchor and old_anchor == new_anchor))))
        if independent:
            continuation = False
        new = previous is None or (kind != "unclassified" and kind != previous["type"])
        if previous and kind == previous["type"] and independent:
            new = True
        if new:
            result.append({"first": number, "last": number, "type": kind, "state": "supported", "reasons": []})
        else:
            previous["last"] = number
            if not continuation:
                previous["state"] = "unresolved"
                previous["reasons"].append(f"Page {number + 1} has only a translation/continuation phrase; review its association."
                                          if weak_association else f"Page {number + 1} has no reliable continuation or new-document marker.")
        anchors = set(_anchors(text, kind))  # OCR may flatten side-by-side records onto one line
        if len(anchors) > 1:
            result[-1]["state"] = "unresolved"
            result[-1]["reasons"].append(f"Page {number + 1} contains different record identifiers; provide each record as a separate readable page.")
    return result


def segments(pages: list[str]) -> list[tuple[int, int, str]]:
    return [(s["first"], s["last"], s["type"]) for s in analyze(pages)]


def context(folder: Path, case_dir: Path | None, names: list[str]) -> dict:
    """I/O boundary: immutable bytes and previously reviewed layouts only."""
    import documents
    if case_dir is not None:
        __import__('source_association').assert_ready(case_dir)
    saved = documents.read(case_dir) if case_dir else None
    return {"hashes": {n: source_hash(Path(folder) / n) for n in names},
            "decisions": (saved or {}).get("boundary_decisions", {})}


def prepare(docs: list[tuple[str, str]], paged: dict | None, ctx: dict | None = None) -> tuple:
    """Pure normalization shared by folder, portal and incremental intake.

    Reconstruct whole original files from padded legacy segment aliases, then
    apply one layout. Missing physical pages in an input are never invented.
    """
    from batch import split_pages
    from classify import classify_text
    ctx, paged = ctx or {}, paged or {}
    originals: dict[str, list[str]] = {}
    for alias, text in docs:
        name = alias.split("#p", 1)[0]
        incoming = paged.get(alias)
        if incoming is None:
            incoming = [text]
        target = originals.setdefault(name, [])
        target.extend([""] * max(0, len(incoming) - len(target)))
        for n, page in enumerate(incoming):
            if page:
                target[n] = page
    normalized, page_map, plans, instances = [], {}, {}, {}
    for name, texts in originals.items():
        content_hash = ctx.get("hashes", {}).get(name)
        # Text-only entry points remain testable, but explicitly do not claim
        # to identify original bytes. Persistent approvals require bytes.
        fingerprint = digest({"sha256": content_hash, "page_texts": texts, "segmenter": SEGMENTER_VERSION})
        layout = analyze(texts)
        decision = ctx.get("decisions", {}).get(name) or {}
        if content_hash and decision.get("fingerprint") == fingerprint and not decision.get("undone"):
            starts = decision["starts_zero_based"]
            layout = [{"first": a, "last": b - 1, "type": classify_text("\n".join(texts[a:b])).doc_type,
                       "state": "reviewed", "reasons": []} for a, b in zip(starts, starts[1:] + [len(texts)])]
        tuples = [(s["first"], s["last"], s["type"]) for s in layout]
        new_docs, new_pages = split_pages(name, texts, tuples)
        for (alias, _), part in zip(new_docs, layout):
            part.update(instance_id=identity(content_hash or digest(texts), part["first"], part["last"]),
                        doc_ids=[alias], source_sha256=content_hash,
                        evidence_fingerprint=digest({"source": content_hash, "range": [part["first"], part["last"]],
                                                     "texts": texts[part["first"]:part["last"] + 1], "segmenter": SEGMENTER_VERSION}))
            instances[alias] = part
        plans[name] = {"file": name, "source_sha256": content_hash, "fingerprint": fingerprint,
                       "segmenter_version": SEGMENTER_VERSION, "page_count": len(texts), "instances": layout,
                       "reviewed_by": {k: decision[k] for k in ("who", "role", "at") if k in decision}
                       if layout and all(s["state"] == "reviewed" for s in layout) else None}
        normalized += new_docs
        page_map.update(new_pages)
    return normalized, page_map, plans, instances


def _saved(case_dir: Path) -> dict:
    import documents
    return documents.read(case_dir) or {}


def stage(case_dir: Path, incoming: dict) -> None:
    """Durable fail-closed marker written before graph/meta exposure.

    A crash or records-write failure leaves a visible hold. The completed
    documents save clears this marker. All state stays in the cataloged store.
    """
    if not incoming:
        return
    import documents
    data = _saved(case_dir) or {"version": documents.VERSION, "built": None, "documents": []}
    data.setdefault("boundary_plans", {}).update(incoming)
    data["boundary_processing"] = sorted(set(data.get("boundary_processing", [])) | set(incoming))
    documents.save(case_dir, data)


def _original_aliases(case_dir: Path) -> set[str]:
    from factgraph import FactGraph
    from classify.patterns import TYPES
    path = case_dir / "fact_graph_raw.json"
    if not path.exists():
        path = case_dir / "fact_graph.json"
    if not path.exists():
        return set()
    return {s.doc_id for f in FactGraph.load(path).all_facts().values() for s in f.sources
            if s.doc_type in TYPES and s.doc_id not in {"portal questionnaire", "office question", "firm_profile.json", "paralegal_review"}
            and not s.from_facts}


def fail_case(case_dir: Path, source_folder: Path, error: str) -> None:
    """Keep the last graph/history and publish a visible failed-run hold."""
    import documents
    import clock
    from factgraph import FactGraph
    data = _saved(case_dir) or {"version": documents.VERSION, "built": None, "documents": []}
    data["boundary_failure"] = {"at": clock.stamp(), "reason": "Document processing did not finish; read this case again."}
    documents.save(case_dir, data)
    path = case_dir / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"client_id": case_dir.name, "source_folder": str(source_folder.resolve())}
    meta.setdefault("errors", {})["__client__"] = error
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    if not (case_dir / "fact_graph.json").exists():
        FactGraph(case_dir.name).save(case_dir / "fact_graph.json")


def views(case_dir: Path) -> list[dict]:
    return read_scope.once(("document-boundaries", str(case_dir.absolute())), lambda: _views(case_dir), copy=True)


def _views(case_dir: Path) -> list[dict]:
    """Current plans, including a hold if original bytes changed on disk."""
    meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8")) if (case_dir / "meta.json").exists() else {}
    folder = Path(meta.get("source_folder") or "")
    result = []
    association_held = __import__('source_association').hold(case_dir)
    if association_held:
        result.append({"file": "Source association", "source_sha256": None, "fingerprint": "association", "page_count": 0,
                       "stale": True, "held": True, "processing_incomplete": True, "reviewed_by": None, "instances": [],
                       "hold_kind": "source_association", "physical_file": False,
                       "reason": "Source setup is incomplete or damaged. An authorized staff member must recover the complete original set."})
    saved = _saved(case_dir)
    if saved.get("boundary_failure"):
        result.append({"file": "Document processing", "source_sha256": None, "fingerprint": "failed", "page_count": 0,
                       "stale": True, "held": True, "processing_incomplete": True, "reviewed_by": None, "instances": []})
    for name, plan in saved.get("boundary_plans", {}).items():
        row = json.loads(json.dumps(plan))
        row["stale"] = not plan.get("source_sha256") or source_hash(folder / name) != plan["source_sha256"]
        independent_processing = name in saved.get("boundary_processing", [])
        row["association_held"] = association_held
        row["independent_processing_incomplete"] = independent_processing
        row["independent_held"] = row["stale"] or independent_processing or any(s["state"] == "unresolved" for s in plan["instances"])
        row["processing_incomplete"] = association_held or independent_processing
        row["held"] = row["stale"] or row["processing_incomplete"] or any(s["state"] == "unresolved" for s in plan["instances"])
        result.append(row)
    # A graph with no recorded boundary evidence cannot establish which
    # original its filename aliases mean. Migrate by reading the source once.
    known = {p["file"] for p in result}
    for record in saved.get("documents", []):
        if not (record.get("pages") or []):
            continue
        for name in record.get("files", []):
            if name in known:
                continue
            known.add(name)
            result.append({"file": name, "source_sha256": None, "fingerprint": "legacy", "page_count": max(record["pages"]),
                           "stale": True, "held": True, "reviewed_by": None,
                           "instances": [{"first": min(record["pages"]) - 1, "last": max(record["pages"]) - 1,
                                          "type": record["type"], "state": "unresolved", "doc_ids": record.get("doc_ids", []),
                                          "reasons": ["This older reading has no recorded boundary evidence. Read the original again."]}]})
    if (case_dir / "fact_graph.json").exists() and folder.is_dir() and meta.get("source_folder"):
        for path in folder.iterdir():
            if path.is_file() and path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"} and path.name not in known:
                result.append({"file": path.name, "source_sha256": None, "fingerprint": "unrecorded", "page_count": 1,
                               "stale": False, "held": True, "pending_read": True, "processing_incomplete": True, "reviewed_by": None,
                               "instances": [{"first": 0, "last": 0, "type": "unclassified", "state": "unresolved", "doc_ids": [path.name],
                                              "reasons": ["No completed boundary reading for this original. Read it again."]}]})
                known.add(path.name)
    # Metadata-poor legacy bundles may have only a graph. The OCR source
    # alias is enough to hold it, even when its original folder is missing.
    for alias in sorted(_original_aliases(case_dir)):
        name = alias.split("#p", 1)[0]
        if name not in known:
            known.add(name)
            result.append({"file": name, "source_sha256": None, "fingerprint": "legacy", "page_count": 0,
                           "stale": True, "held": True, "reviewed_by": None,
                           "instances": [{"first": 0, "last": 0, "type": "unclassified", "state": "unresolved", "doc_ids": [alias],
                                          "reasons": ["The original evidence identity is missing. Locate and read the original again."]}]})
    return result


def problems(case_dir: Path) -> list[str]:
    out = []
    for p in views(case_dir):
        if not p["held"]:
            continue
        if p.get("hold_kind") == "source_association":
            out.append(p["reason"])
            continue
        if p.get("association_held") and not p.get("independent_held"):
            continue
        processing = p.get("independent_processing_incomplete", p.get("processing_incomplete"))
        out.append(f"Review document boundaries in Documents: {p['file']}" +
                   (" (document processing did not finish; read it again)." if processing else
                    " (the original changed or cannot be reached; read it again)." if p["stale"] else "."))
    return out


def held_aliases(case_dir: Path) -> set[str]:
    plans = views(case_dir)
    return ((_original_aliases(case_dir) if _saved(case_dir).get("boundary_failure") or any(p.get("hold_kind") == "source_association" for p in plans) else set()) |
            {p["file"] for p in plans if p["stale"] or p.get("processing_incomplete")} |
            {alias for p in plans for s in p["instances"] if p["stale"] or s["state"] == "unresolved" for alias in s["doc_ids"]})


def without_sources(graph, aliases: set[str]):
    """Rebuild affected facts and their dependents; preserve unrelated reviews."""
    from factgraph import FactGraph
    def excluded(alias):
        return alias in aliases or alias.split("#p", 1)[0] in aliases
    impacted = {k for k, f in graph.all_facts().items() if any(excluded(s.doc_id) for s in f.sources)}
    while True:
        expanded = impacted | {k for k, f in graph.all_facts().items()
                               if set(f.derived_from) & impacted or any(set(s.from_facts) & impacted for s in f.sources)}
        if expanded == impacted:
            break
        impacted = expanded
    fresh = FactGraph(graph.client_id)
    for key, fact in graph.all_facts().items():
        if key not in impacted:
            fresh._facts[key] = fact
            continue
        for s in fact.sources:
            if not excluded(s.doc_id) and not (set(s.from_facts) & impacted) and not fact.derived_by:
                fresh.add_source(key, s.doc_id, s.doc_type, s.raw_value, s.normalized_value, s.confidence,
                                 tier=fact.tier, page=s.page, from_facts=s.from_facts,
                                 instance_id=s.instance_id, subject_role=s.subject_role,
                                 evidence_version=s.evidence_version, input_evidence=s.input_evidence,
                                 read_manifest=s.read_manifest, reading_issues=s.reading_issues)
    graph._facts = fresh._facts
    return impacted


def independent_absence(iid: str, entry: dict) -> bool:
    """A named absence report does not approve the OCR facts on its card."""
    paper = (entry.get("absence") or {}).get("paper")
    return bool(paper and iid == f"absent:{paper}" and entry.get("action") == "absent"
                and entry.get("item", {}).get("kind") == "absence"
                and entry.get("item", {}).get("id") == iid)


def invalidate(case_dir: Path, incoming: dict) -> None:
    """Keep history while reopening approvals whose original evidence changed."""
    from factgraph import FactGraph
    import clock
    old = _saved(case_dir).get("boundary_plans", {})
    aliases = set()
    for name, plan in incoming.items():
        previous = old.get(name)
        if previous and previous.get("fingerprint") == plan.get("fingerprint") and previous["instances"] == plan["instances"]:
            continue
        oldparts = (previous or {}).get("instances", [])
        newparts = {p["evidence_fingerprint"]: p for p in plan["instances"] if p["state"] != "unresolved"}
        aliases.update(a for p in oldparts if p["evidence_fingerprint"] not in newparts for a in p["doc_ids"])
        if not previous:  # migration: legacy page aliases cannot prove a reviewed boundary
            aliases.add(name)
    graphpath = case_dir / "fact_graph.json"
    decisionspath = case_dir / "decisions.json"
    if not aliases or not graphpath.exists() or not decisionspath.exists():
        return
    affected = without_sources(FactGraph.load(graphpath), aliases)
    decisions = json.loads(decisionspath.read_text(encoding="utf-8"))
    changed = False
    for iid, entry in decisions.items():
        if not entry.get("undone") and not independent_absence(iid, entry) and set(entry.get("item", {}).get("facts", [])) & affected:
            entry["undone"] = {"who": "The document reader", "at": clock.stamp(),
                               "reason": "Original document evidence or boundaries changed; review the new sources."}
            changed = True
    if changed:
        decisionspath.write_text(json.dumps(decisions, indent=2, ensure_ascii=False), encoding="utf-8")


def boundary_snapshot(case_dir: Path, name: str, expected: str) -> bytes:
    """Caller holds authority/case gates; OCR may then read these bytes without them."""
    plan = _saved(case_dir).get("boundary_plans", {}).get(name)
    if not plan or expected != plan["fingerprint"]:
        raise ValueError("The document changed. Refresh Documents before reviewing its boundaries.")
    meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
    folder = Path(meta["source_folder"])
    if Path(name).name != name or source_hash(folder / name) != plan["source_sha256"]:
        raise ValueError("The original changed or cannot be reached. Read it again first.")
    data = (folder / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != plan["source_sha256"]:
        raise ValueError("The original changed or cannot be reached. Read it again first.")
    return data


def resolve(case_dir: Path, name: str, starts: str, expected: str, who: str, role: str | None, undo: bool = False) -> None:
    """Named whole-source layout decision; reprocessing is synchronous and safe.

    Starts are 1-based for people and zero-based in the stored record. Existing
    independently anchored documents cannot be merged through this control.
    """
    import clock
    import documents
    import jobs
    from classify import extract_pages
    from inbox import _reprocess_documents
    if not who.strip():
        raise ValueError("Enter your name first: every boundary decision records who made it.")
    with jobs.case_lock(jobs.folder_for(case_dir.parent), case_dir.name):
        data = _saved(case_dir)
        plan = data.get("boundary_plans", {}).get(name)
        if not plan or expected != plan["fingerprint"]:
            raise ValueError("The document changed. Refresh Documents before reviewing its boundaries.")
        meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
        folder = Path(meta["source_folder"])
        if Path(name).name != name or source_hash(folder / name) != plan["source_sha256"]:
            raise ValueError("The original changed or cannot be reached. Read it again first.")
        texts = extract_pages(folder / name)
        fresh = prepare([(name, "\n".join(texts))], {name: texts}, context(folder, None, [name]))[2][name]
        if fresh["fingerprint"] != expected:
            raise ValueError("The document reading changed. Read it again before reviewing its boundaries.")
        try:
            numbers = [int(x.strip()) - 1 for x in starts.split(",")]
        except ValueError:
            raise ValueError("Enter document start pages separated by commas, beginning with 1.") from None
        if not undo and (not numbers or numbers[0] != 0 or numbers != sorted(set(numbers)) or numbers[-1] >= len(texts)):
            raise ValueError("Start pages must begin with 1, increase, and stay within the original scan.")
        mandatory = {p["first"] for p in fresh["instances"]}
        if not undo and any("different record identifiers" in reason for p in fresh["instances"] for reason in p["reasons"]):
            raise ValueError("Different records share a physical page. Provide each record as a separate readable page first.")
        if not undo and not mandatory.issubset(numbers):
            raise ValueError("Keep each separately identified document start; this control can split uncertain pages further.")
        decision = {"fingerprint": expected, "starts_zero_based": numbers, "who": who, "role": role,
                    "at": clock.stamp(), "undone": undo}
        old = data.setdefault("boundary_decisions", {}).get(name, {})
        decision["history"] = old.get("history", []) + [{k: v for k, v in decision.items() if k != "history"}]
        data["boundary_decisions"][name] = decision
        documents.save(case_dir, data)
        # If processing fails the new decision cannot clear the old plan/hold.
        _reprocess_documents(case_dir, folder, [(name, "\n".join(texts))], {name: {"pages": len(texts)}},
                             None, "folder", who, {name: texts})
