"""Microsoft 365 -- SharePoint document libraries and OneDrive -- through
Microsoft Graph, as a document source and (optionally) a result sink.

Layout expected (a setting): one folder in a SharePoint document library
(or a OneDrive) holding one sub-folder per client; that folder's files (and,
if set, one named sub-folder such as "Documents") are the client's documents.

Authentication: an app registration in the firm's Microsoft Entra ID
(tenant id, client id, client secret in environment variables), using the
client-credentials flow -- the integration acts as itself, not as a person.
Least privilege: the Graph application permission `Sites.Selected`, with
the admin granting read access to the one SharePoint site that holds the
client folders (write only if results go back). Word documents are
converted to PDF by Microsoft (`?format=pdf`); downloads follow Graph's
redirect to a pre-authenticated link.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import firmsecrets  # keys and secrets are read by name (src/firmsecrets.py)

from .base import RemoteClient, RemoteDoc

GRAPH = "https://graph.microsoft.com/v1.0"
# Office files Microsoft converts to PDF on download; everything else as is.
CONVERT = {".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".ppt", ".pptx"}
DEFAULTS: dict[str, Any] = {
    "site": None,                  # "firm.sharepoint.com:/sites/Clients" -- or set drive_id directly
    "library": "Documents",        # the site's document library holding the client folders
    "drive_id": None,              # a library's or a OneDrive's drive id (skips site lookup)
    "root_path": None,             # e.g. "Clients/SIJS": the folder holding one sub-folder per client
    "documents_subfolder": None,   # e.g. "Documents"
    "results_folder_name": None,   # e.g. "I-485 packet" (None = never write)
    "env": {"tenant": "MS_TENANT_ID", "client_id": "MS_CLIENT_ID"},  # the client secret is a secret: read by name (src/firmsecrets.py)
}


class GraphError(RuntimeError):
    pass


class Microsoft365:
    name = "microsoft"

    def __init__(self, settings: dict[str, Any] | None = None, env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None):
        self.s = {**DEFAULTS, **(settings or {})}
        env = dict(os.environ if env is None else env)
        self.secret = {k: env.get(v, "") for k, v in self.s["env"].items()}
        self.secret["client_secret"] = firmsecrets.get("microsoft.client_secret", env=env) or ""
        names = {"client_secret": firmsecrets.BY_NAME["microsoft.client_secret"].env[0], **self.s["env"]}
        missing = [names[k] for k in ("tenant", "client_id", "client_secret") if not self.secret[k]]
        if missing:
            raise GraphError(f"Microsoft 365 isn't set up: missing environment variable(s) {', '.join(missing)} (docs/integrations.md).")
        if not (self.s["drive_id"] or self.s["site"]):
            raise GraphError("Microsoft 365 isn't set up: set microsoft.site (or drive_id) in schemas/registers/connectors.json.")
        if not self.s["root_path"]:
            raise GraphError("Microsoft 365 isn't set up: set microsoft.root_path (the folder holding one folder per client).")
        self.http = httpx.Client(transport=transport, timeout=60)
        self._token: tuple[str, float] | None = None
        self._drive: str | None = self.s["drive_id"]

    # -- transport -------------------------------------------------------------------

    def token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        r = self.http.post(f"https://login.microsoftonline.com/{self.secret['tenant']}/oauth2/v2.0/token",
                           data={"grant_type": "client_credentials", "client_id": self.secret["client_id"],
                                 "client_secret": self.secret["client_secret"], "scope": "https://graph.microsoft.com/.default"})
        if r.status_code != 200:
            raise GraphError(f"Microsoft refused the app registration ({r.status_code}): check the tenant, client id and secret.")
        body = r.json()
        self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return self._token[0]

    def call(self, method: str, url: str, **kw) -> httpx.Response:
        url = url if url.startswith("http") else GRAPH + url
        extra = kw.pop("headers", {})
        for attempt in range(4):
            r = self.http.request(method, url, headers={"Authorization": f"Bearer {self.token()}", **extra}, **kw)
            if r.status_code in (429, 503) and attempt < 3:  # Graph throttling: wait as told, then retry
                time.sleep(float(r.headers.get("Retry-After", 2 * (attempt + 1))))
                continue
            if r.status_code >= 400:
                raise GraphError(f"Microsoft Graph {method} {url.split('?')[0].replace(GRAPH, '')} -> {r.status_code}")
            return r
        raise GraphError("Microsoft Graph kept throttling")

    def drive(self) -> str:
        """The drive (document library) id, looked up once from the site."""
        if self._drive:
            return self._drive
        site = self.call("GET", f"/sites/{self.s['site']}").json()
        drives = self.call("GET", f"/sites/{site['id']}/drives").json().get("value", [])
        lib = next((d for d in drives if d.get("name", "").lower() == self.s["library"].lower()), None)
        if lib is None:
            raise GraphError(f"No document library named {self.s['library']!r} on {self.s['site']}.")
        self._drive = lib["id"]
        return self._drive

    def children(self, item: str | None = None, path: str | None = None) -> list[dict[str, Any]]:
        where = f"/drives/{self.drive()}/root:/{quote(path.strip('/'))}:/children" if path else f"/drives/{self.drive()}/items/{item}/children"
        out, url = [], where + "?$top=999"
        while url:
            body = self.call("GET", url).json()
            out += body.get("value", [])
            url = body.get("@odata.nextLink")
        return out

    # -- source ----------------------------------------------------------------------

    def clients(self) -> list[RemoteClient]:
        return [RemoteClient(id=i["id"], name=i["name"], raw=i) for i in self.children(path=self.s["root_path"]) if "folder" in i]

    def documents(self, client: RemoteClient) -> list[RemoteDoc]:
        folder = client.id
        if self.s["documents_subfolder"]:
            sub = next((i for i in self.children(client.id) if "folder" in i and i["name"].lower() == self.s["documents_subfolder"].lower()), None)
            if sub is None:
                return []
            folder = sub["id"]
        docs = []
        for i in self.children(folder):
            if "file" not in i:
                continue
            convert = Path(i["name"]).suffix.lower() in CONVERT
            docs.append(RemoteDoc(id=i["id"], name=Path(i["name"]).stem + ".pdf" if convert else i["name"],
                                  mime="application/pdf" if convert else i["file"].get("mimeType", ""), size=i.get("size"),
                                  modified=i.get("lastModifiedDateTime"), checksum=i.get("cTag") or i.get("eTag"), raw=i))
        return docs

    def download(self, client: RemoteClient, doc: RemoteDoc) -> bytes:
        convert = Path(doc.raw.get("name", "")).suffix.lower() in CONVERT
        r = self.call("GET", f"/drives/{self.drive()}/items/{doc.id}/content" + ("?format=pdf" if convert else ""), follow_redirects=False)
        if r.status_code in (301, 302, 303, 307) and r.headers.get("location"):
            r = self.http.get(r.headers["location"])  # a pre-authenticated link: no token sent
            if r.status_code != 200:
                raise GraphError(f"Microsoft 365 document {doc.id}: download failed ({r.status_code})")
        return r.content

    # -- sink (only with results_folder_name set; needs write access to the site) ----------

    def publish(self, client: RemoteClient, files: list[Path], note: str) -> dict[str, Any]:
        name = self.s["results_folder_name"]
        if not name:
            return {"published": False, "why": "microsoft.results_folder_name is not set"}
        folder = next((i for i in self.children(client.id) if "folder" in i and i["name"] == name), None)
        if folder is None:
            folder = self.call("POST", f"/drives/{self.drive()}/items/{client.id}/children",
                               json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}).json()
        ids = []
        for filename, payload, mime in [(f.name, f.read_bytes(), "application/pdf") for f in files] + [("NOTE.txt", note.encode(), "text/plain")]:
            r = self.call("PUT", f"/drives/{self.drive()}/items/{folder['id']}:/{quote(filename)}:/content", content=payload,
                          headers={"Content-Type": mime})
            ids.append(r.json().get("id"))
        return {"published": True, "folder": folder["id"], "files": ids}
