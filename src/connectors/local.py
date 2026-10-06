"""Today's setup as a connector: a folder per client (clients/<id>/source),
and results that stay where they are. Lets the rest be written once against
the connector interface."""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import RemoteClient, RemoteDoc


class LocalFolderSource:
    name = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def clients(self) -> list[RemoteClient]:
        return [RemoteClient(id=p.name, name=p.name) for p in sorted(self.root.iterdir()) if (p / "source").is_dir()]

    def documents(self, client: RemoteClient) -> list[RemoteDoc]:
        out = []
        for p in sorted((self.root / client.id / "source").iterdir()):
            if p.is_file():
                st = p.stat()
                out.append(RemoteDoc(id=p.name, name=p.name, size=st.st_size, checksum=hashlib.md5(p.read_bytes()).hexdigest(),
                                     modified=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()))
        return out

    def download(self, client: RemoteClient, doc: RemoteDoc) -> bytes:
        return (self.root / client.id / "source" / doc.id).read_bytes()


class NoSink:
    """Results stay in data/clients/<id> (today's behaviour)."""
    name = "none"

    def publish(self, client: RemoteClient, files: list[Path], note: str) -> dict[str, Any]:
        return {"published": False}


class FolderSink:
    """Copies results into a folder per client -- e.g. a synced shared drive."""
    name = "folder"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def publish(self, client: RemoteClient, files: list[Path], note: str) -> dict[str, Any]:
        dest = self.root / client.name
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / f.name)
        (dest / "NOTE.txt").write_text(note, encoding="utf-8")
        return {"published": True, "folder": str(dest), "files": [f.name for f in files]}
