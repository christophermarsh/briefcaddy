"""What the restore drill's tests (tests/test_restore_drill.py, tests/e2e/test_restore_drill.py) share: an archive made different, with a manifest that agrees with it."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


def tamper(archive: Path, name: str, data: bytes | None) -> None:
    """The archive as if it had been made with this file different (or without it): its manifest agrees, so the backup itself opens cleanly."""
    with zipfile.ZipFile(archive) as zf:
        entries = {i.filename: zf.read(i.filename) for i in zf.infolist()}
    manifest = json.loads(entries["manifest.json"])
    if data is None:
        entries.pop(name)
        manifest["files"] = [f for f in manifest["files"] if f["path"] != name]
    else:
        entries[name] = data
        for f in manifest["files"]:
            if f["path"] == name:
                f.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    entries["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
        for n, b in entries.items():
            out.writestr(n, b)
