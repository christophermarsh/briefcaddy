"""Attorney-reviewed client file, reusing export_firm's one-case gather/writer.

The firm export remains complete. This boundary selects client material only,
keeps an attorney-only exclusion list, and binds approval to the delivered bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import inspect as inspect_module
import json
import os
import re
import sys
from functools import wraps

import oslock
import zipfile
from pathlib import Path

import signing_evidence

COVER = {
    "en": ("Your file from the office", "DRAFT: cover wording awaits attorney approval.", "This file contains your documents, filed forms and letters selected by the attorney. Contact the office about any missing document."),
    "pt": ("Seu arquivo do escritório", "RASCUNHO: o texto da capa aguarda aprovação do advogado.", "Este arquivo contém seus documentos, formulários protocolados e cartas selecionados pelo advogado. Entre em contato com o escritório se faltar algum documento."),
    "es": ("Su archivo de la oficina", "BORRADOR: el texto de la portada espera aprobación del abogado.", "Este archivo contiene sus documentos, formularios presentados y cartas seleccionados por el abogado. Comuníquese con la oficina si falta algún documento."),
    "ht": ("Dosye ou nan biwo a", "BOUYON TRADIKSYON MACHIN: avoka ak yon tradiktè sètifye dwe apwouve tèks sa a.", "Dosye sa a gen dokiman ou yo, fòm ki depoze yo ak lèt avoka a chwazi. Kontakte biwo a si yon dokiman manke."),
}

# These operational/security records are never client documents, even when an
# erroneous catalog registers one as a source. Notes have a separate sanitized
# work-product rendering below; no arbitrary product JSON is released.
PROTECTED = frozenset({"profile.json", "answers.json", "access.json", "auth.json", "sessions.json", "users.json",
    "status.json", "case_assignment.json", "restricted.json", "conflicts.json", "meta.json", "documents.json",
    "fact_graph.json", "fact_graph_raw.json", "decisions.json", "engagement.json", "electronic_consent.json",
    "fact_graph_reviewed.json",
    "communication_consent.json", "requests.json", "portal_access.json", "portal_promotion.json", "source-association.json",
    "client_file_policy.json", "purge.json", "events.jsonl", "journey.json", "client-file.lock", ".client-file-policy.part"})
SOURCE_FORMATS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".docx", ".txt"})
WORK_FORMATS = frozenset({".pdf", ".docx", ".txt", ".html", ".htm"})
MAX_INVENTORY_BYTES = 128 * 1024 * 1024


PRIVATE_DIRS = frozenset({"auth", "sessions", "users", "acl", "consent-evidence", "language-evidence",
    "communication-attempts", "staff-upload-receipts", "processing-receipts", "source-authorization",
    "source-authorizations", "portal-promotion", "family-operations", "communication-stop"})


def internal(area, rel):
    parts = tuple(part.casefold() for part in Path(rel).parts)
    # Security directories remain private in an erroneous registered source or
    # portal upload too. Area/source classification cannot override this deny.
    return bool(area == "reader_examples" or (area == "portal" and not rel.startswith("uploads/"))
                or any(part in PRIVATE_DIRS or part in PROTECTED for part in parts)
                or Path(rel).suffix.casefold() in (".lock", ".part", ".tmp", ".sqlite", ".db", ".jsonl"))


def allowed(client_dir: Path) -> None:
    """Q1 owns policy. A pending destruction blocks all bundle operations."""
    import engagement
    import purge

    d = Path(client_dir)
    state = purge.entry(d.parent, d.name) or {}
    if (not d.is_dir() or d.is_symlink() or state.get("state") in ("waiting", "running", "done", "purged")
            or engagement.read(d).get("destroyed") or any(r.get("case") == d.name for r in engagement.destroyed(d.parent))):
        raise ValueError("Client file unavailable while destruction is pending or recorded. Cancel a waiting purge through its existing attorney workflow first.")


def directory_safe(path: Path) -> bool:
    import stat

    try:
        for part in (path, *path.parents):
            if part.exists() and (part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) &
                                  getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
                return False
        return True
    except OSError:
        return False


def serialized(fn):
    @wraps(fn)
    def change(client_dir, *args, **kwargs):
        arguments = inspect_module.signature(fn).bind(client_dir, *args, **kwargs)
        if arguments.arguments.get("role") not in (None, "attorney"):
            raise PermissionError("Client-file preparation, approval and handover are attorney actions.")
        if not str(arguments.arguments.get("who") or "").strip():
            raise ValueError("Enter your name first.")
        d = Path(client_dir)
        if not directory_safe(d):
            raise ValueError("Case folder cannot use a link or reparse point.")
        from portal.communication_consent import data_gate
        import jobs
        with data_gate(d.parent.parent), jobs.case_lock(jobs.folder_for(d.parent), d.name, timeout=30):
            allowed(d)
            with oslock.locked(d / "client-file.lock", timeout=30, poll=0.02):
                allowed(d)
                return fn(client_dir, *args, **kwargs)
    return change


def safe(path: Path, root: Path) -> bool:
    """No link, junction or ancestor escape, including Windows reparse points."""
    import stat

    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
        for part in (path, *path.parents):
            info = part.lstat()
            if part.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                return False
        return path.is_file()
    except (OSError, ValueError):
        return False


def capture(path, root, limit=MAX_INVENTORY_BYTES):
    """Read one bounded, regular, same-generation file; never follow a link."""
    import stat
    path = Path(path)
    if not safe(path, Path(root)):
        raise ValueError("Unsafe client-file or evidence source.")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Client-file or evidence source exceeds the supported review size.")
        raw = stream.read(limit + 1)
        current = path.stat()
        if (len(raw) > limit or not safe(path, Path(root))
                or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) !=
                   (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) or len(raw) != info.st_size):
            raise ValueError("Client-file or evidence source changed during its read.")
        return raw


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _source_scope(d, portal_root):
    """The own source pointer never authorizes another case or firm store."""
    meta = d / "meta.json"
    if not meta.exists() and not meta.is_symlink():
        return None, None
    raw = capture(meta, d, 1024 * 1024)
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("source_folder", ""), str):
        raise ValueError("Current source-folder metadata needs review.")
    named = data.get("source_folder") or ""
    if not named:
        return None, hashlib.sha256(raw).hexdigest()
    original = Path(named)
    if not directory_safe(original):
        raise ValueError("Current source folder is unsafe; review its case association.")
    source = original.resolve()
    case = d.resolve()
    own_uploads = (Path(portal_root) / "clients" / d.name / "uploads").resolve()
    def inside(path, root):
        return path == root or root in path.parents
    own = inside(source, case) or inside(source, own_uploads)
    protected = (d.parent.resolve(), Path(portal_root).resolve(), d.parent.parent.resolve())
    # The installation's ordinary external document tree also has case slots;
    # a missing peer meta pointer never makes another slot this case's source.
    external_roots = {(d.parent.parent.parent / "clients").resolve()}
    configured = os.environ.get("I485_CLIENTS_ROOT")
    if configured:
        external_roots.add(Path(configured).resolve())
    for root in external_roots:
        if inside(source, root) and not inside(source, root / d.name):
            raise ValueError("Source-folder association overlaps another protected case's document slot.")
    if not own and any(inside(source, root) or inside(root, source) for root in protected):
        raise ValueError("Source-folder association overlaps a protected case, portal or firm store; review an own external document folder.")
    return source, hashlib.sha256(raw).hexdigest()


def cover(name: str, lang: str) -> bytes:
    from pypdf import PdfWriter
    from fill.continuation import MARGIN
    from review.bundle import _Sheet, _wrap

    page, y = _Sheet(), 730
    for text in (*COVER.get(lang, COVER["en"]), name):
        for part in _wrap(text, 12, 510):
            page.text(MARGIN, y, part, "F3", 12)
            y -= 18
        y -= 20
    writer = PdfWriter()
    writer.add_page(page.to_page(writer))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def gather(client_dir: Path, portal_root: Path) -> tuple[list, list, list]:
    import engagement

    allowed(client_dir)
    d, cid = Path(client_dir), Path(client_dir).name
    own_source, meta_sha = _source_scope(d, portal_root)
    sys.path.append(str(Path(__file__).resolve().parents[1] / "tools"))
    import export_firm

    entries, skipped, warnings, roots = export_firm.gather(argparse.Namespace(
        all=False, client=cid, data=d.parent, portal=portal_root, firm_files=False))
    if _source_scope(d, portal_root) != (own_source, meta_sha):
        raise ValueError("The source-folder association changed during gathering; review it again.")
    # A PDF copy with the exact recorded filing fingerprint carries every form
    # within it. Rebuilt drafts never stand in for a filed packet.
    status = _read(d / "status.json", {})
    filed = status.get("filings") or []
    hashes = {r.get("bundle_sha256") if r.get("online") else r.get("packet_sha256") for r in filed}
    hashes.discard(None)
    found, selected, excluded = set(), [], []
    source_root = Path(_read(d / "meta.json", {}).get("source_folder") or d)
    registered = {str(f).split("#", 1)[0] for doc in _read(d / "documents.json", {}).get("documents", []) for f in doc.get("files", [])}
    signed_paths = set()
    for letter in engagement.read(d).get("letters", []):
        if (letter.get("signature") or {}).get("how") == "portal":
            result = signing_evidence.verify(d, letter["id"])
            if not result["ok"]:
                warnings.append("A signed engagement letter is unverifiable; review its signing evidence before preparing a file.")
                continue
            pdf, snapshot, meta = signing_evidence.paths(d, letter["id"])
            raw = pdf.read_bytes()
            if signing_evidence.digest(raw) != result["letter_sha256"]:
                raise ValueError("Signed snapshot changed during file preparation.")
            selected.append(export_firm.Entry(f"letters/signed-{letter['id']}.pdf", "Immutable signed engagement letter", data=raw))
            selected.append(export_firm.Entry(f"letters/signing-certificate-{letter['id']}.pdf", "Firm signing evidence certificate", data=signing_evidence.certificate(d, letter["id"])))
            signed_paths.update((pdf, snapshot, meta))
    letters = engagement.read(d).get("letters", [])
    released = [l for l in letters if l.get("signature") or l.get("sent") or
                (l.get("kind") != "engagement" and l.get("approved"))]
    letter_paths = {engagement.pdf_path(d, l) for l in released}
    letter_paths |= {d / "engagement" / l["signature"]["file"] for l in released if (l.get("signature") or {}).get("file")}
    for entry in entries:
        path = entry.source
        if entry.arcname.startswith("documents/") and (own_source is None or not safe(path, own_source)):
            raise ValueError("A document does not belong to the current own source folder.")
        if path is None or not any(safe(path, root) for root in roots):
            excluded.append({"path": entry.arcname, "why": "Unsafe source or link; attorney must inspect"})
            warnings.append("A source file could not be safely included.")
            continue
        if path in signed_paths:
            continue
        parts = entry.arcname.split("/", 2)
        area, rel = parts[0], parts[2] if len(parts) == 3 else ""
        if internal(area, rel):
            why = "Internal jurisdiction policy, recipient authority or interrupted write" if rel == "client_file_policy.json" else "Internal case, authority or security record; never client material"
            excluded.append({"path": entry.arcname, "why": why})
            continue
        if area == "clients" and rel == "notes.json":
            excluded.append({"path": entry.arcname, "why": "Case notes require explicit review of their sanitized work-product copy"})
            continue
        if path.suffix.lower() in (".json", ".jsonl", ".sqlite", ".db"):
            excluded.append({"path": entry.arcname, "why": "Structured material requires a reviewed client-safe rendering; no raw product record is released"})
            continue
        if area == "clients" and rel in ("client_file_policy.json", ".client-file-policy.part", "client-file.lock"):
            excluded.append({"path": entry.arcname, "why": "Internal jurisdiction policy, recipient authority or interrupted write"})
            continue  # an erroneous document registration never releases legal/security authority
        if area == "clients" and (rel == "status.json" or re.fullmatch(r"\.family-status-[0-9a-f]{32}\.part", rel)):
            excluded.append({"path": entry.arcname, "why": "Internal case status and family identity/operation record"})
            continue  # a malformed document registration cannot publish related case IDs
        if area == "clients" and rel in ("case_assignment.json", "case_assignment.json.part"):
            excluded.append({"path": entry.arcname, "why": "Internal staff assignment record or interrupted write"})
            continue  # never a client document, even if an erroneous record registers it
        if area == "clients" and (rel in ("source-association.json", "portal_promotion.json") or re.fullmatch(r"(?:source-association|portal_promotion)\.json\.[0-9a-f]{16}\.tmp", rel)):
            excluded.append({"path": entry.arcname, "why": "Internal staff source setup/handover record or interrupted write"})
            continue
        if area in ("clients", "portal") and (rel in ("communication_consent.json", "requests.json", "portal_access.json", "portal_promotion.json")
                or re.fullmatch(r"(communication_consent|requests|portal_access|portal_promotion)\.json\.[0-9a-f]{16}\.tmp", rel)
                or rel.startswith(("consent-evidence/", "language-evidence/", "communication-attempts/"))):
            excluded.append({"path": entry.arcname, "why": "Internal service-message approval, review evidence or dispatch record"})
            continue  # malformed document registration cannot release internal approvals
        is_source = area == "documents" or (area == "portal" and rel.startswith("uploads/"))
        is_source |= area == "clients" and (rel in registered or path.parent == source_root and path.name in registered)
        # Arbitrary registered ZIPs may contain security or other-case bytes.
        # Only the existing exact filed-fingerprint path can qualify a ZIP.
        if path.suffix.lower() == ".zip":
            is_source = False
        is_letter = path in letter_paths
        is_filed = False
        if area == "clients" and path.suffix.lower() in (".pdf", ".zip") and not is_source and not is_letter:
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
            is_filed = fingerprint in hashes
            if is_filed:
                found.add(fingerprint)
        if is_source or is_letter or is_filed:
            selected.append(entry)
        else:
            excluded.append({"path": entry.arcname, "why": "Firm work product, internal record or unfiled draft"})
    if len(found) != len(hashes) or any(not (r.get("bundle_sha256") if r.get("online") else r.get("packet_sha256")) for r in filed):
        warnings.append("An exact filed packet or online bundle is unavailable. Restore the filed copy before approval; current drafts cannot substitute.")
    if not filed and status.get("filed_at"):
        warnings.append("A legacy filed mark has no exact filed copy fingerprint. Restore and record the filed copy before approval.")
    if skipped:
        warnings.append("The source exporter skipped links or files being written; review before approval.")
    selected.insert(0, export_firm.Entry("COVER.pdf", "Client-language draft cover", data=cover(engagement.client_name(d, portal_root), engagement.language(d, portal_root))))
    # Source paths in export warnings can name other cases; the client's manifest
    # carries only a neutral request to contact the office.
    return selected, excluded, list(dict.fromkeys(warnings))


def _notes(raw):
    """Own authored text with correction provenance, without related case IDs.

    An attorney must review whether to include this work product. The original
    internal record, access flags and carried-prospect pointers are not exported.
    """
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("notes"), list):
        raise ValueError("Case notes need a readable current record before inventory review.")
    lines = ["Case notes selected for attorney review", ""]
    for row in data["notes"]:
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("id", "text", "by", "at")):
            raise ValueError("A case note is malformed; review it before client-file preparation.")
        lines.extend([f"{row['id']} — {row['by']} — {row['at']}", row["text"]])
        if row.get("corrects"):
            if not isinstance(row["corrects"], str) or not re.fullmatch(r"n\.\d+", row["corrects"]):
                raise ValueError("Case-note correction provenance is malformed.")
            lines.append("Corrects note " + row["corrects"])
        lines.append("")
    return "\n".join(lines).encode()


def _inventory(client_dir, portal_root):
    """Capture reviewed candidates once; archive the same bytes that were hashed."""
    import export_firm
    import client_file_policy
    d = Path(client_dir)
    scope_before = _source_scope(d, portal_root)
    selected, excluded, warnings = gather(d, portal_root)
    existing = {entry.arcname for entry in selected}
    all_entries, _, _, roots = export_firm.gather(argparse.Namespace(
        all=False, client=d.name, data=d.parent, portal=Path(portal_root), firm_files=False))
    if _source_scope(d, portal_root) != scope_before:
        raise ValueError("Current source-folder association changed; review the inventory again.")
    own_source = scope_before[0]
    for entry in all_entries:
        if entry.arcname.startswith("documents/") and entry.source is not None and (own_source is None or not safe(entry.source, own_source)):
            raise ValueError("Current source-folder association is unavailable; review an own source folder.")
    entries = {entry.arcname: (entry, "material", entry.arcname == "COVER.pdf") for entry in selected}
    for entry in all_entries:
        area, _, rel = entry.arcname.split("/", 2)
        if entry.arcname in existing or internal(area, rel) or entry.source is None:
            continue
        if not any(safe(entry.source, root) for root in roots):
            continue
        suffix = entry.source.suffix.lower()
        if area != "clients":
            entries[entry.arcname] = (entry, "unsupported", False)
        elif rel == "notes.json":
            entries[entry.arcname] = (entry, "work_product_notes", False)
        elif suffix in WORK_FORMATS:
            entries[entry.arcname] = (entry, "work_product", False)
        elif suffix not in (".json", ".lock", ".part", ".tmp", ".db", ".sqlite", ".jsonl") or rel not in PROTECTED:
            # Unsupported data remain visible for an explicit exclusion or a
            # client-safe alternative copy; no automatic arbitrary JSON export.
            entries[entry.arcname] = (entry, "unsupported", False)
    rows, captured, total = [], {}, 0
    for arc, (entry, category, required) in sorted(entries.items()):
        if entry.source is None:
            raw = entry.data
        else:
            root = next(root for root in roots if safe(entry.source, root))
            raw = capture(entry.source, root)
        total += len(raw)
        if total > MAX_INVENTORY_BYTES:
            raise ValueError("The inventory exceeds the supported review size; arrange a reviewed alternative delivery.")
        original_hash = hashlib.sha256(raw).hexdigest()
        original_size = len(raw)
        output_path = arc
        if category == "work_product_notes":
            raw = _notes(raw)
            output_path = f"clients/{d.name}/CASE-NOTES.txt"
        if entry.source is not None and category == "material" and entry.source.suffix.lower() not in SOURCE_FORMATS | WORK_FORMATS | {".zip"}:
            category = "unsupported"
        row = {"path": arc, "output_path": output_path, "category": category, "bytes": len(raw),
               "sha256": hashlib.sha256(raw).hexdigest(), "input_sha256": original_hash, "input_bytes": original_size,
               "required": required, "reviewable": category != "unsupported", "inclusion": "pending", "reason": ""}
        rows.append(row)
        captured[arc] = export_firm.Entry(output_path, "Attorney-reviewed " + category.replace("_", " "), data=raw)
    if _source_scope(d, portal_root) != scope_before:
        raise ValueError("Current source-folder association changed during capture; review again.")
    out = {"snapshot_sha256": client_file_policy.digest({"entries": rows, "warnings": warnings,
            "source_scope": {"metadata_sha256": scope_before[1],
                             "root_sha256": None if scope_before[0] is None else client_file_policy.digest(str(scope_before[0]))}}),
           "entries": rows, "warnings": warnings, "excluded": excluded}
    return out, captured


def inventory(client_dir, portal_root):
    return _inventory(client_dir, portal_root)[0]


def reviewed_entries(client_dir, portal_root, approval):
    current, captured = _inventory(client_dir, portal_root)
    if not isinstance(approval, dict) or approval.get("inventory_sha256") != current["snapshot_sha256"]:
        raise ValueError("Current client-file inventory changed; review the exact current files before preparation.")
    choices = {row["path"]: row for row in approval["decisions"]}
    selected, excluded = [], list(current["excluded"])
    for row in current["entries"]:
        choice = choices.get(row["path"])
        if choice is None or choice["sha256"] != row["sha256"] or choice["input_sha256"] != row["input_sha256"]:
            raise ValueError("Current client-file bytes changed; review the inventory again.")
        if choice["include"]:
            if not row["reviewable"]:
                raise ValueError("Unsupported material requires a reviewed client-safe alternative copy.")
            selected.append(captured[row["path"]])
        else:
            if row["required"]:
                raise ValueError("The current client cover cannot be excluded.")
            excluded.append({"path": row["path"], "why": choice["reason"]})
    return selected, excluded, current["warnings"]


def inspect(path: Path, expected: str) -> list[dict]:
    """Inspect one bounded captured archive generation against its retained digest."""
    return inspect_bytes(capture(path, path.parent), expected)


def inspect_bytes(raw: bytes, expected: str) -> list[dict]:
    """Verify the exact bytes about to be delivered, without reopening a path."""
    if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("The prepared client file changed or is missing. Prepare and approve a fresh file.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > MAX_INVENTORY_BYTES + 1024 * 1024:
                raise ValueError("The prepared client file exceeds its supported bounded contents.")
            manifest = json.loads(archive.read("manifest.json"))
            for row in manifest["files"]:
                if hashlib.sha256(archive.read(row["path"])).hexdigest() != row["sha256"]:
                    raise ValueError("A client-file manifest fingerprint does not match.")
            return manifest["files"]
    except (zipfile.BadZipFile, KeyError, TypeError) as exc:
        raise ValueError("The prepared client file manifest is unreadable; prepare and review it again.") from exc
