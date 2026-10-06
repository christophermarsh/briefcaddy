"""Mirrors a document source into the folders the pipeline already reads
(clients/<local id>/source), downloading only what is new or changed.

  - one local folder per remote client; its name is derived from the remote
    name and id (stable, filesystem-safe) and recorded in sync_state.json;
  - photos (JPEG/PNG) become one-page PDFs, as portal uploads do; files the
    pipeline can't read (spreadsheets, videos) are skipped and listed;
  - a document deleted at the source is reported, never deleted here -- a
    person decides;
  - a file is replaced only when its fingerprint (checksum, size, modified
    time) changes, so a re-sync touches nothing and the overnight run sees
    no change (overnight.py compares the folder's own signature).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Any

from .base import DocumentSource, RemoteClient, RemoteDoc
import clock
import events
import oslock

SOURCE_NAMES = {"google_drive": "Google Drive", "microsoft": "Microsoft 365", "filevine": "Filevine", "docketwise": "Docketwise", "clio": "Clio"}  # the ledger's words
READABLE = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}


def _slug(text: str, limit: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:limit] or "client"


def local_id(client: RemoteClient, source: str) -> str:
    """'Ana Clara Souza' / Filevine 1234 -> 'ana_clara_souza-fv1234' (the portal's client-id pattern)."""
    prefix = {"filevine": "fv", "google_drive": "gd", "microsoft": "ms", "docketwise": "dw", "clio": "cl"}.get(source, source[:2])
    # numeric ids stay readable (fv1234); others (Drive ids are case-sensitive) become a short hash, so two never share a folder
    tail = client.id if client.id.isdigit() else hashlib.sha1(client.id.encode()).hexdigest()[:10]
    return f"{_slug(client.name)}-{prefix}{tail}"


def _filename(doc: RemoteDoc) -> str:
    stem, dot, ext = doc.name.rpartition(".")
    stem = stem if dot else doc.name
    safe = re.sub(r"[^\w\- ]+", "_", unicodedata.normalize("NFKC", stem)).strip()[:80] or "document"
    return f"{safe} [{re.sub(r'[^A-Za-z0-9]', '', doc.id)[:16]}].pdf"  # the id keeps two "passport.pdf" apart


def _kind(doc: RemoteDoc) -> str | None:
    mime = (doc.mime or "").lower()
    if mime in READABLE:
        return mime
    ext = doc.name.lower().rsplit(".", 1)[-1]
    return {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext)


def _plain(path: Path) -> None:
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            if part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError("Mirrored documents must use plain installation-owned paths.")


def mirror(source: DocumentSource, clients_root: Path, only: list[str] | None = None, home: Path | None = None,
           local_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """Serialize every caller's state update; optional explicit existing-case mapping.

    The selected-intake entry point supplies local_ids. Legacy callers retain
    their existing only=None/[] semantics and derived folder names. No case lock
    is taken here: worker case lock precedes this destination mirror lock.
    """
    clients_root = Path(clients_root).absolute()
    _plain(clients_root)
    _plain(clients_root / "sync_state.json")
    _plain(clients_root / "sync_state.lock")
    if local_ids is not None:
        if not only or not isinstance(local_ids, dict) or set(local_ids) != set(only) or len(only) != len(set(only)):
            raise ValueError("Explicit mirror mapping requires a nonempty exact selected set.")
        for local in local_ids.values():
            if not isinstance(local, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,100}", local):
                raise ValueError("Invalid local case mapping.")
            for path in (clients_root / local, clients_root / local / "source"):
                _plain(path)
                if not path.resolve().is_relative_to(clients_root.resolve()):
                    raise ValueError("Local case mapping leaves this installation.")
        if len(set(local_ids.values())) != len(local_ids):
            raise ValueError("Different remote clients cannot share one local case.")
    clients_root.mkdir(parents=True, exist_ok=True)
    with oslock.locked(clients_root / "sync_state.lock", timeout=60):
        if local_ids is not None:
            state_path = clients_root / "sync_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"clients": {}}
            for remote, local in local_ids.items():
                old = state["clients"].get(local)
                if old and (old.get("remote_id") != remote or old.get("source") != source.name):
                    raise ValueError("This local case is already bound to a different or unverified source.")
                if any(k != local and v.get("remote_id") == remote and v.get("source") == source.name for k, v in state["clients"].items()):
                    raise ValueError("This remote client is already bound to another local case.")
        return _mirror(source, clients_root, only, home, local_ids)


def _mirror(source: DocumentSource, clients_root: Path, only: list[str] | None = None, home: Path | None = None,
            local_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """One pass over every client of the source; returns what changed. home: the firm's data folder, where the event ledger is (src/events.py); clients_root
    is where the mirrored documents go, which as installed is not inside it (the repo's clients/ folder)."""
    state_path = clients_root / "sync_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"clients": {}}
    report: dict[str, Any] = {"source": source.name, "at": clock.stamp(), "clients": {}}
    for client in source.clients():
        if only and client.id not in only:
            continue
        lid = local_ids[client.id] if local_ids is not None else local_id(client, source.name)
        folder = clients_root / lid / "source"
        folder.mkdir(parents=True, exist_ok=True)
        known = state["clients"].setdefault(lid, {"remote_id": client.id, "name": client.name, "source": source.name, "docs": {}})
        changes = {"added": [], "updated": [], "skipped": [], "gone": []}
        remote = source.documents(client)
        for doc in remote:
            kind = _kind(doc)
            if kind is None:
                changes["skipped"].append(doc.name)
                continue
            seen = known["docs"].get(doc.id)
            if seen and seen["fingerprint"] == doc.fingerprint() and (folder / seen["file"]).exists():
                continue
            data = source.download(client, doc)
            if kind != "application/pdf":
                from portal.store import image_to_pdf

                data = image_to_pdf(data)
            name = _filename(doc)
            tmp = folder / f".{name}.part"
            tmp.write_bytes(data)
            os.replace(tmp, folder / name)
            if seen and seen["file"] != name:  # renamed at the source: one copy, under the new name
                (folder / seen["file"]).unlink(missing_ok=True)
            changes["updated" if seen else "added"].append(name)
            known["docs"][doc.id] = {"file": name, "fingerprint": doc.fingerprint(), "name": doc.name}
        ids = {d.id for d in remote}
        changes["gone"] = [v["file"] for k, v in known["docs"].items() if k not in ids]
        report["clients"][lid] = {"remote_id": client.id, "name": client.name, **changes}
        if changes["added"] or changes["updated"]:
            shown = SOURCE_NAMES.get(source.name) or source.name.replace("_", " ").title()
            events.record("imports", "synced", f"Copied in {len(changes['added'])} new and {len(changes['updated'])} changed document(s) from {shown}",
                          case=lid, home=home, default_who=(shown, "system", "connector"))
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, state_path)
    return report
