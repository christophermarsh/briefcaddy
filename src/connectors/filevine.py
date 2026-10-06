"""Filevine (REST API v2) as a document source and a result sink.

  in:   the firm's SIJS projects (one project type, optionally one phase) ->
        their documents, downloaded for the pipeline;
  out:  the packet / filled forms uploaded into the project (tagged), and a
        note saying what was done -- optionally a phase change.

Authentication: a Personal Access Token (created by a Filevine account admin)
plus a client id and secret, exchanged for a short-lived bearer token; every
call also carries the org and user ids (x-fv-orgid, x-fv-userid). Secrets
come from environment variables only.

Checked against Filevine's own pages on 2026-10-02 (docs/integrations.md has the list, and what was
readable): the token exchange (identity.filevine.com, grant personal_access_token, the scope string), the
gateway https://api.filevineapp.com and the x-fv-orgId header, and the SHAPE of an upload: Filevine's "Create
Document URL for Upload" page is described (by its search-result summary: the page body itself did not render)
as POST /fv-app/v2/Documents (JSON: Filename, OrgID, Size) returning a document id and a temporary S3 address
that lives 5 hours, to which the file is PUT. Not a multipart POST to the project, which is what this connector
did before. The step after that, adding the document to the project, was described only in a partner support
article's summary: its path /fv-app/v2/Projects/<project id>/Documents/<document id> is UNCONFIRMED, as are its
method and body. Filevine's developer pages are a script-rendered site: only their titles could be read here.

STILL NOT VERIFIED (no sandbox account exists; UNCONFIRMED below lists each one): the add-to-project path,
method and body, the exact response and body field names, the project/contact/note paths, paging, the webhook
header, and the gateway's address (see base_url).
They are all settings (schemas/registers/connectors.json -> filevine.paths / fields), so confirming them on the firm's
account with `python -m connectors.check filevine` is a configuration change, not a code change.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any

import httpx
import firmsecrets  # keys and secrets are read by name (src/firmsecrets.py)

from .base import RemoteClient, RemoteDoc

# Each is a guess until the firm's own Filevine account (or a Filevine sandbox) has answered it; docs/integrations.md.
UNCONFIRMED = (
    "the add-to-project step's path (/fv-app/v2/Projects/{projectId}/Documents/{documentId}: from a partner article's summary, not a readable Filevine page), its method (POST assumed) and body (folderId, tags)",
    "the gateway's address: api.filevineapp.com is what Filevine's authentication pages are described as giving, but its base URL page is said to use a tenant-specific address ({company}.api.filevineapp.com) that the tenant scope exists to look up; base_url is a setting for that reason",
    "the names in the upload answer (the document id, the S3 address) and in the list answers (items, hasMore, ids)",
    "the project, project-document list, contact and note paths (the /fv-app/v2 prefix and the Documents upload paths are from Filevine's pages)",
    "the x-fv-userid header (Filevine's pages name x-fv-orgId; the user id is kept as before)",
    "the webhook signature header",
)

DEFAULTS: dict[str, Any] = {
    "base_url": "https://api.filevineapp.com",  # a setting: UNCONFIRMED whether a firm's own address is {company}.api.filevineapp.com (see UNCONFIRMED)
    "identity_url": "https://identity.filevine.com/connect/token",
    "scope": "fv.api.gateway.access tenant filevine.v2.api.* openid email fv.auth.tenant.read",
    "env": {"client_id": "FILEVINE_CLIENT_ID", "org_id": "FILEVINE_ORG_ID", "user_id": "FILEVINE_USER_ID"},  # the token and the client secret are secrets: read by name (src/firmsecrets.py)
    "project_type_id": None,     # the SIJS project type (ask the firm)
    "phase_name": None,          # only projects in this phase, if set
    "upload_folder_id": None,    # where results go inside the project (None = the project's root)
    "upload_tags": ["i485-pipeline"],
    "set_phase": {},             # pipeline stage -> Filevine phase name, e.g. {"ready": "Ready to File"}; empty = never change phases
    "page_size": 200,
    "paths": {"projects": "/fv-app/v2/Projects", "project": "/fv-app/v2/Projects/{projectId}", "documents": "/fv-app/v2/Projects/{projectId}/Documents",
              "document": "/fv-app/v2/Documents/{documentId}", "notes": "/fv-app/v2/Projects/{projectId}/Notes", "contact": "/fv-app/v2/Contacts/{contactId}",
              # the upload, in Filevine's three steps: ask for an address (POST), PUT the bytes there, add the document to the project
              # upload_url: described by Filevine's Create Document URL page; add_document: UNCONFIRMED (path, method and body)
              "upload_url": "/fv-app/v2/Documents", "add_document": "/fv-app/v2/Projects/{projectId}/Documents/{documentId}"},
    "add_document_method": "POST",  # UNCONFIRMED
    "fields": {"download_url": ["downloadUrl", "url", "locator"], "items": "items", "has_more": "hasMore",
               # the JSON the "Create Document URL" page names: Filename, OrgID, Size (UploaderId is optional and left out)
               "upload_body": {"filename": "Filename", "org_id": "OrgID", "size": "Size"},
               "upload_answer_id": ["documentId", "documentID", "DocumentId", "id"],
               "upload_answer_url": ["url", "uploadUrl", "uploadURL", "signedUrl", "presignedUrl"]},
    "webhook": {"signature_header": "X-Filevine-Signature", "algorithm": "sha256"},
}


class FilevineError(RuntimeError):
    pass


def _first(body: Any, names: list[str]) -> Any:
    """The first of these keys the answer has (Filevine's casing is one of the unconfirmed things); an id that
    arrives as an object ({"native": 5}) is read as its native value."""
    for name in names:
        value = body.get(name) if isinstance(body, dict) else None
        if isinstance(value, dict) and "native" in value:
            value = value["native"]
        if value not in (None, ""):
            return value
    return None


class Filevine:
    name = "filevine"

    def __init__(self, settings: dict[str, Any] | None = None, env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None):
        self.s = {**DEFAULTS, **(settings or {})}
        self.s["paths"] = {**DEFAULTS["paths"], **(settings or {}).get("paths", {})}
        self.s["fields"] = {**DEFAULTS["fields"], **(settings or {}).get("fields", {})}
        env = dict(os.environ if env is None else env)
        self.secret = {k: env.get(v, "") for k, v in self.s["env"].items()}
        self.secret["pat"] = firmsecrets.get("filevine.pat", env=env) or ""
        self.secret["client_secret"] = firmsecrets.get("filevine.client_secret", env=env) or ""
        names = {"pat": firmsecrets.BY_NAME["filevine.pat"].env[0], "client_secret": firmsecrets.BY_NAME["filevine.client_secret"].env[0], **self.s["env"]}
        missing = [names[k] for k in ("pat", "client_id", "client_secret", "org_id", "user_id") if not self.secret[k]]
        if missing:
            raise FilevineError(f"Filevine isn't set up: missing environment variable(s) {', '.join(missing)} (docs/integrations.md).")
        self.http = httpx.Client(transport=transport, timeout=60)
        self._token: tuple[str, float] | None = None

    # -- transport ---------------------------------------------------------------

    def token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        r = self.http.post(self.s["identity_url"], data={"grant_type": "personal_access_token", "token": self.secret["pat"],
                                                         "client_id": self.secret["client_id"], "client_secret": self.secret["client_secret"],
                                                         "scope": self.s["scope"]})
        if r.status_code != 200:
            raise FilevineError(f"Filevine sign-in refused ({r.status_code}): check the token, client id and secret.")
        body = r.json()
        self._token = (body["access_token"], time.time() + int(body.get("expires_in", 1200)))
        return self._token[0]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}", "x-fv-orgid": str(self.secret["org_id"]), "x-fv-userid": str(self.secret["user_id"])}

    def call(self, method: str, path: str, **kw) -> httpx.Response:
        url = path if path.startswith("http") else self.s["base_url"].rstrip("/") + path
        for attempt in range(4):
            r = self.http.request(method, url, headers=self._headers(), **kw)
            if r.status_code == 429 and attempt < 3:  # Filevine meters with a token bucket: wait and retry
                time.sleep(float(r.headers.get("Retry-After", 2 * (attempt + 1))))
                continue
            if r.status_code >= 400:
                raise FilevineError(f"Filevine {method} {path} -> {r.status_code}")
            return r
        raise FilevineError("Filevine kept rate-limiting")

    def pages(self, path: str, params: dict[str, Any] | None = None):
        """Every item of a paged list (offset/limit, until hasMore is false)."""
        f, offset = self.s["fields"], 0
        while True:
            body = self.call("GET", path, params={**(params or {}), "offset": offset, "limit": self.s["page_size"]}).json()
            items = body.get(f["items"], []) if isinstance(body, dict) else body
            yield from items
            if not (isinstance(body, dict) and body.get(f["has_more"])) or not items:
                return
            offset += len(items)

    # -- source ------------------------------------------------------------------

    def clients(self) -> list[RemoteClient]:
        params = {k: v for k, v in (("projectTypeId", self.s["project_type_id"]), ("phaseName", self.s["phase_name"])) if v}
        out = []
        for p in self.pages(self.s["paths"]["projects"], params):
            if p.get("isArchived"):
                continue
            out.append(RemoteClient(id=str(p["projectId"]), name=p.get("projectName") or str(p["projectId"]), raw=p))
        return out

    def contact(self, client: RemoteClient) -> RemoteClient:
        """The project's client contact: phone and email for portal invitations."""
        cid = client.raw.get("clientId")
        if not cid:
            return client
        c = self.call("GET", self.s["paths"]["contact"].format(contactId=cid)).json()
        phones = c.get("phones") or [] if isinstance(c.get("phones"), list) else []
        emails = c.get("emails") or [] if isinstance(c.get("emails"), list) else []
        first = lambda xs, k: next((x.get(k) for x in xs if isinstance(x, dict) and x.get(k)), "")  # noqa: E731
        client.phone = c.get("phone") or first(phones, "number")
        client.email = c.get("email") or first(emails, "address")
        return client

    def documents(self, client: RemoteClient) -> list[RemoteDoc]:
        return [RemoteDoc(id=str(d["documentId"]), name=d.get("filename") or str(d["documentId"]), mime=d.get("contentType") or "",
                          size=d.get("size"), modified=d.get("uploadDate") or d.get("modifiedDate"),
                          checksum=str(d.get("version") or ""), raw=d)
                for d in self.pages(self.s["paths"]["documents"].format(projectId=client.id))]

    def download(self, client: RemoteClient, doc: RemoteDoc) -> bytes:
        meta = self.call("GET", self.s["paths"]["document"].format(documentId=doc.id)).json()
        url = next((meta.get(k) for k in self.s["fields"]["download_url"] if meta.get(k)), None)
        if not url:
            raise FilevineError(f"Filevine gave no download link for document {doc.id} (fields tried: {self.s['fields']['download_url']}).")
        r = self.http.get(url)  # a pre-signed link: no Filevine headers
        if r.status_code != 200:
            raise FilevineError(f"Filevine document {doc.id}: download failed ({r.status_code})")
        return r.content

    # -- sink ----------------------------------------------------------------------

    def upload(self, project_id: str, path: Path) -> Any:
        """One file into a project, in three steps (steps 1 and 2 as the Create Document URL for Upload page is described, 2026-10-02):
        1. POST the document's name, size and the org id: the answer holds the document's id and a temporary S3 address;
        2. PUT the file's bytes to that address (S3's own link: none of Filevine's headers go there);
        3. add the document to the project (this step's path, method and body are UNCONFIRMED, see UNCONFIRMED)."""
        f, data = self.s["fields"], path.read_bytes()
        names = f["upload_body"]
        made = self.call("POST", self.s["paths"]["upload_url"],
                         json={names["filename"]: path.name, names["org_id"]: str(self.secret["org_id"]), names["size"]: len(data)}).json()
        document_id, address = _first(made, f["upload_answer_id"]), _first(made, f["upload_answer_url"])
        if not document_id or not address:
            raise FilevineError(f"Filevine's answer to the upload request had no document id or upload address (fields tried: "
                                f"{f['upload_answer_id']} and {f['upload_answer_url']}).")
        put = self.http.put(address, content=data)
        if put.status_code >= 300:
            raise FilevineError(f"Filevine's storage refused the file {path.name} ({put.status_code}).")
        body: dict[str, Any] = {"tags": self.s["upload_tags"]}
        if self.s["upload_folder_id"]:
            body["folderId"] = str(self.s["upload_folder_id"])
        self.call(self.s["add_document_method"], self.s["paths"]["add_document"].format(projectId=project_id, documentId=document_id), json=body)
        return document_id

    def publish(self, client: RemoteClient, files: list[Path], note: str, stage: str | None = None) -> dict[str, Any]:
        uploaded = [self.upload(client.id, f) for f in files]
        self.call("POST", self.s["paths"]["notes"].format(projectId=client.id),
                  json={"body": note, "kind": "note", "attachedDocuments": [d for d in uploaded if d]})
        phase = self.s["set_phase"].get(stage or "")
        if phase:
            self.call("PATCH", self.s["paths"]["project"].format(projectId=client.id), json={"phaseName": phase})
        return {"published": True, "documents": uploaded, "phase": phase or None}


def verify_webhook(body: bytes, signature: str, signing_key: str, algorithm: str = "sha256") -> bool:
    """A webhook call is from Filevine when its signature matches the
    subscription's signing key (HMAC of the raw body). The header name and
    encoding are settings until confirmed on the firm's account."""
    expected = hmac.new(signing_key.encode(), body, getattr(hashlib, algorithm)).hexdigest()
    given = signature.split("=", 1)[-1].strip().lower()
    return hmac.compare_digest(expected, given)
