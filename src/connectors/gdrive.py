"""Google Drive (API v3) as a document source and, optionally, a result sink.

Layout expected (a setting): one root folder -- usually in a Shared Drive --
holding one sub-folder per client; that folder's files (and, if set, one
named sub-folder such as "Documents") are the client's documents.

Authentication: a Google Cloud service account (its JSON key file named by
GOOGLE_SERVICE_ACCOUNT_FILE). Either the root folder is shared with the
service account's address, or the Workspace admin grants it domain-wide
delegation and GOOGLE_IMPERSONATE names the user it acts as. Read-only
scope unless results are written back. The access token is signed here with
the `cryptography` package (RS256 JWT -> oauth2 token endpoint), so no
Google SDK is needed.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import firmsecrets  # keys and secrets are read by name (src/firmsecrets.py)

from .base import RemoteClient, RemoteDoc

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
# Google's own formats are exported as PDF; everything else is downloaded as is.
EXPORT = {"application/vnd.google-apps.document": "application/pdf", "application/vnd.google-apps.spreadsheet": "application/pdf",
          "application/vnd.google-apps.presentation": "application/pdf"}
DEFAULTS: dict[str, Any] = {
    "root_folder_id": None,        # the folder holding one sub-folder per client
    "documents_subfolder": None,   # e.g. "Documents": read only that sub-folder of each client folder
    "results_folder_name": None,   # e.g. "I-485 packet": where results are written (None = never write)
    "env": {"impersonate": "GOOGLE_IMPERSONATE"},  # the service account's key is a secret: read by name (src/firmsecrets.py)
}


class DriveError(RuntimeError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def service_account_jwt(key: dict[str, Any], scope: str, subject: str | None = None, now: float | None = None) -> str:
    """The signed assertion Google exchanges for an access token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(now or time.time())
    claims = {"iss": key["client_email"], "scope": scope, "aud": key.get("token_uri", "https://oauth2.googleapis.com/token"),
              "iat": now, "exp": now + 3600, **({"sub": subject} if subject else {})}
    signing_input = f"{_b64(json.dumps({'alg': 'RS256', 'typ': 'JWT', 'kid': key.get('private_key_id', '')}).encode())}.{_b64(json.dumps(claims).encode())}"
    private = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    return f"{signing_input}.{_b64(private.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256()))}"


class GoogleDrive:
    name = "google_drive"

    def __init__(self, settings: dict[str, Any] | None = None, env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None,
                 write: bool = False, data_root: Path | None = None):
        self.s = {**DEFAULTS, **(settings or {})}
        env = dict(os.environ if env is None else env)
        text = firmsecrets.get("gdrive.service_account", env=env, data_root=data_root)  # explicit firm vault when supplied
        if not text:
            raise DriveError(f"Google Drive isn't set up: {firmsecrets.BY_NAME['gdrive.service_account'].file_env} must name the service account's key file (docs/integrations.md).")
        if not self.s["root_folder_id"]:
            raise DriveError("Google Drive isn't set up: set google_drive.root_folder_id in schemas/registers/connectors.json.")
        self.key = json.loads(text)
        self.subject = env.get(self.s["env"]["impersonate"]) or None
        self.scope = "https://www.googleapis.com/auth/drive" if write else "https://www.googleapis.com/auth/drive.readonly"
        self.http = httpx.Client(transport=transport, timeout=60)
        self._token: tuple[str, float] | None = None

    def token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        r = self.http.post(self.key.get("token_uri", "https://oauth2.googleapis.com/token"),
                           data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                 "assertion": service_account_jwt(self.key, self.scope, self.subject)})
        if r.status_code != 200:
            raise DriveError(f"Google refused the service account ({r.status_code}): check the key and the folder sharing.")
        body = r.json()
        self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return self._token[0]

    def call(self, method: str, url: str, **kw) -> httpx.Response:
        r = self.http.request(method, url, headers={"Authorization": f"Bearer {self.token()}"}, **kw)
        if r.status_code >= 400:
            raise DriveError(f"Google Drive {method} {url.split('?')[0]} -> {r.status_code}")
        return r

    def children(self, folder_id: str) -> list[dict[str, Any]]:
        out, page = [], None
        while True:
            params = {"q": f"'{folder_id}' in parents and trashed = false", "pageSize": 1000,
                      "fields": "nextPageToken, files(id, name, mimeType, size, modifiedTime, md5Checksum)",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", **({"pageToken": page} if page else {})}
            body = self.call("GET", f"{API}/files", params=params).json()
            out += body.get("files", [])
            page = body.get("nextPageToken")
            if not page:
                return out

    # -- source ------------------------------------------------------------------

    def clients(self) -> list[RemoteClient]:
        return [RemoteClient(id=f["id"], name=f["name"], raw=f) for f in self.children(self.s["root_folder_id"]) if f["mimeType"] == FOLDER]

    def documents(self, client: RemoteClient) -> list[RemoteDoc]:
        folder = client.id
        if self.s["documents_subfolder"]:
            sub = next((f for f in self.children(client.id) if f["mimeType"] == FOLDER and f["name"].lower() == self.s["documents_subfolder"].lower()), None)
            if sub is None:
                return []
            folder = sub["id"]
        docs = []
        for f in self.children(folder):
            if f["mimeType"] == FOLDER:
                continue
            exported = f["mimeType"] in EXPORT
            docs.append(RemoteDoc(id=f["id"], name=f["name"] + (".pdf" if exported else ""), mime="application/pdf" if exported else f["mimeType"],
                                  size=int(f["size"]) if f.get("size") else None, modified=f.get("modifiedTime"),
                                  checksum=f.get("md5Checksum") or f.get("modifiedTime"), raw=f))
        return docs

    def download(self, client: RemoteClient, doc: RemoteDoc) -> bytes:
        native = doc.raw.get("mimeType")
        if native in EXPORT:
            return self.call("GET", f"{API}/files/{doc.id}/export", params={"mimeType": EXPORT[native]}).content
        return self.call("GET", f"{API}/files/{doc.id}", params={"alt": "media", "supportsAllDrives": "true"}).content

    # -- sink (only with write=True and results_folder_name set) ---------------------

    def publish(self, client: RemoteClient, files: list[Path], note: str) -> dict[str, Any]:
        name = self.s["results_folder_name"]
        if not name:
            return {"published": False, "why": "google_drive.results_folder_name is not set"}
        folder = next((f for f in self.children(client.id) if f["mimeType"] == FOLDER and f["name"] == name), None)
        if folder is None:
            folder = self.call("POST", f"{API}/files", params={"supportsAllDrives": "true"},
                               json={"name": name, "mimeType": FOLDER, "parents": [client.id]}).json()
        ids = []
        for f in [*files, None]:
            payload, filename, mime = (f.read_bytes(), f.name, "application/pdf") if f else (note.encode(), "NOTE.txt", "text/plain")
            meta = json.dumps({"name": filename, "parents": [folder["id"]]})
            boundary = "i485pipelineboundary"
            body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode() \
                + payload + f"\r\n--{boundary}--".encode()
            r = self.call("POST", UPLOAD, params={"uploadType": "multipart", "supportsAllDrives": "true"}, content=body,
                          headers={"Content-Type": f"multipart/related; boundary={boundary}"})
            ids.append(r.json().get("id"))
        return {"published": True, "folder": folder["id"], "files": ids}
