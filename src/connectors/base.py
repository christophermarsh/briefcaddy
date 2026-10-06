"""What every connector provides. A document source lists the firm's SIJS
clients and their documents and downloads them; a result sink puts the
pipeline's output back (the packet, a note) where the firm works."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class RemoteClient:
    id: str              # the other system's id (Filevine project id, Drive folder id)
    name: str            # as shown there; becomes the local client folder name via sync.local_id
    phone: str = ""
    email: str = ""
    language: str = "pt"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RemoteDoc:
    id: str
    name: str
    mime: str = ""
    size: int | None = None
    modified: str | None = None
    checksum: str | None = None  # changes when the content changes (Drive md5; Filevine version)
    raw: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return f"{self.checksum or ''}|{self.size or ''}|{self.modified or ''}"


class DocumentSource(Protocol):
    name: str

    def clients(self) -> list[RemoteClient]: ...

    def documents(self, client: RemoteClient) -> list[RemoteDoc]: ...

    def download(self, client: RemoteClient, doc: RemoteDoc) -> bytes: ...


class ResultSink(Protocol):
    name: str

    def publish(self, client: RemoteClient, files: list[Path], note: str) -> dict[str, Any]: ...
