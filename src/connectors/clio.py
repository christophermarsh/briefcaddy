"""Clio Manage (API v4) beside the pipeline: the firm keeps its practice
management in Clio; our cases, packets, stages and deadlines appear there,
and its matters, clients and documents appear here.

  in:   each open or pending matter in a practice area the attorney mapped
        (Settings, Connections) -> a case folder (clients/<name>-cl<matter
        id>/source, via sync.mirror: only new or changed documents) and a
        portal client (name, phone, email, the language from a contact field
        the firm names, the office from the responsible attorney or the
        practice area, the questionnaire by practice area);
  out:  the filing packet (final, not a draft), its review bundle and each
        mailing record as documents on the matter (a rebuilt packet is a new
        version of the same Clio document), the case's stage as a matter
        note when it changes, and every deadline src/journey.py knows as a
        calendar entry on the matter, moved when the date moves and marked
        "Done:" (or "No longer due:") when it is met (a deadline a person set
        on the case goes the same way); a task from the case's tasks as a Clio
        task (assigned to the Clio user with the same e-mail, else to the
        connected user with the person's name in its description; marked
        complete when done); and a case's end state as a matter note, never a
        change to the matter's status in Clio. Nothing is ever deleted
        on either side: there is no DELETE in this module except the one for
        the webhook subscription, and call() refuses every other
        (docs/integrations.md). What went, per case: clio_sent.py.
        Clio's webhooks (clio_hooks.py) tell the product a document or a
        matter changed, so it reads that matter within the hour instead of
        waiting for the night.
  never: a protected case (documents.case_confidentiality: 8 U.S.C. 1367,
        8 CFR 208.6) sends nothing out unless the attorney allowed that case
        (held_back). Billing, trust and payments are never read or written.

What Clio's own documentation says, read 10/02/2026 (every endpoint, field,
scope and limit below is copied from these pages; nothing is guessed):

  - Authorization, https://docs.developers.clio.com/api-docs/clio-manage/authorization/
    OAuth 2.0 authorization code grant. GET https://app.clio.com/oauth/authorize
    with client_id, response_type=code, redirect_uri (must match one of the
    app's redirect URLs), state ("No, but recommended"), redirect_on_decline
    ("true": a declined grant comes back to redirect_uri with
    error=access_denied and the state). The code "is valid for 10 minutes".
    POST https://app.clio.com/oauth/token, application/x-www-form-urlencoded:
    client_id, client_secret, grant_type=authorization_code, code,
    redirect_uri -> token_type "bearer", access_token, expires_in 2592000
    (30 days), refresh_token. Refresh: the same URL with client_id,
    client_secret, grant_type=refresh_token, refresh_token -> a new
    access_token and expires_in (the sample answer carries no new refresh
    token, so the one we hold is kept). "Refresh tokens do not expire; as a
    result, they should be encrypted and stored securely." An expired token
    gets 401. POST https://app.clio.com/oauth/deauthorize (Authorization:
    Bearer, token=<the access token>) -> 200, no body. Tokens issued by
    app.clio.com (US) are not valid in the EU, CA or AU regions.
  - Regions, https://docs.developers.clio.com/handbook/getting-started/regions/
    US https://app.clio.com/api/v4 (also EU, CA, AU prefixes). The page lists
    each region's API address but not its OAuth address, so only the US
    region is built here (the firm's offices are in Massachusetts and
    Florida); another region is left out until Clio documents its OAuth URLs.
  - Permissions, https://docs.developers.clio.com/api-docs/clio-manage/permissions/
    Scopes are the app's "access permissions", chosen per resource as read or
    read/write when the app is created; a user who authorized the app keeps
    those permissions until they authorize again. Missing permission: 403
    ForbiddenError; an association the token can't see comes back with
    "redacted": true. Read access to Matters "also allows you access to
    related endpoints, like Practice Areas". The page gives no list of the
    permission names in the portal, so the setup guide asks, in plain words,
    for read access to matters, contacts, users, practice areas and custom
    fields, and read and write access to documents, notes and calendars;
    which portal checkbox covers notes, users and custom fields is NOT
    VERIFIED (the Test button and a 403 say when one is missing). Write is
    the documentation's "read, create, update, and destroy": we never destroy.
  - Rate limits, https://docs.developers.clio.com/api-docs/clio-manage/rate-limits/
    "Requests to API v4 are rate-limited by access token." "Clio's API has a
    default rate limit of 50 requests per minute during peak usage hours"
    (US: 04:00-19:00 Pacific, Monday to Friday); more off-peak, by an amount
    not published. Every answer carries X-RateLimit-Limit, X-RateLimit-
    Remaining and X-RateLimit-Reset (a unix time); over the limit: 429 with
    Retry-After (seconds). "We do not support custom rate limit increases."
    (This confirms the research note's "50 requests a minute".)
  - Pagination, https://docs.developers.clio.com/api-docs/clio-manage/paging/
    200 a page at most; order=id(asc) and no offset is cursor paging, the
    next page's full URL in meta.paging.next, absent on the last page.
  - Fields, https://docs.developers.clio.com/api-docs/clio-manage/fields/
    fields=a,b,nested{x,y}; one level of nesting (a second level is a 400).
  - Versioning, https://docs.developers.clio.com/api-docs/clio-manage/api-versioning-policy/
    and the changelog https://docs.developers.clio.com/api-docs/clio-manage/api-changelog/
    The X-API-VERSION header pins a minor version (an invalid or retired one
    is 410 Gone); 4.0.13 (2025-10-06) is the default since 2026-01-06, so we
    pin it. Support lasts at least a year after a newer default; 90 days'
    notice by email before a version is retired.
  - Private apps, https://docs.developers.clio.com/handbook/getting-started/building-private-apps/
    and https://docs.developers.clio.com/api-docs/clio-manage/applications/
    A firm builds its own app in the developer portal (developers.clio.com)
    while signed in to its paid Clio account; "Apps and integrations,
    including private apps, are not available on the EasyStart pricing
    tier". The app has a name, website URL, redirect URIs and the access
    permissions; its key and secret are the client id and secret.
  - The API reference's OpenAPI file, https://docs.developers.clio.com/openapi.json
    (servers: https://app.clio.com/api/v4 ...), for each call used here:
      GET  /users/who_am_i.json             fields id,name,email,default_calendar_id
      GET  /practice_areas.json             fields id,name
      GET  /users.json                      fields id,name,enabled,subscription_type
      GET  /custom_fields.json              parent_type=contact; fields id,name,field_type
      GET  /calendars.json                  writeable=true; fields id,name,type
      GET  /matters.json                    practice_area_id, status=open,pending, fields, order, limit, page_token
                                            Matter: id, display_number, description, status, client{...},
                                            practice_area{id,name}, responsible_attorney{id,name}
      GET  /contacts/{id}.json              custom_field_ids[], fields custom_field_values{id,value,field_name}
      GET  /documents.json                  matter_id, fields; Document: id, name, filename, content_type, size,
                                            updated_at, latest_document_version{id}, external_properties{name,value}
      GET  /documents/{id}/download.json    "Will return a 303 See Other redirecting to the download URL"
      POST /documents.json                  data{name, parent{id, type "Matter"|"Document"}, content_type,
                                            external_properties[{name,value}] (at most 5)}; then PUT the file to
                                            latest_document_version.put_url with its put_headers (the signed URL
                                            "expires in 10 minutes"); then PATCH /documents/{id}.json
                                            data{uuid, fully_uploaded: true} (UploadTimeoutError: retry).
                                            A parent of type "Document" makes a new version of that document.
      POST /notes.json                      data{type "Matter", matter{id}, subject, detail, date}
      POST /calendar_entries.json           data{summary, start_at, end_at, all_day, calendar_owner{id}, matter{id},
                                            description, external_properties[{name,value}]}
      PATCH /calendar_entries/{id}.json     the same fields. "Files uploaded to Clio's document integrations
                                            (e.g. Google Drive and Office365) are inaccessible through the API."
      Added for brief J3 (10/03/2026), from the same openapi.json read again that day:
      GET  /matters/{id}.json               one matter, the same fields
      GET  /users.json                      also the field email (User: email, enabled)
      POST /tasks.json                      data{name, description, due_at, matter{id}, assignee{id, type "User"|"Contact"},
                                            priority, status, notify_assignee}; required: name and description, and exactly one of
                                            assignee or assignees ("Exactly one of the two is required when creating a Task"): a task
                                            can't be made without an assignee
      PATCH /tasks/{id}.json                data{status "complete"} ("Users without advanced tasks are allowed to select Complete or
                                            Pending only")
      POST /webhooks.json                   data{url (https only), model, events, fields, expires_at}; model one of activity, bill,
                                            calendar_entry, clio_payments_payment, communication, contact, document, folder, matter,
                                            task; events created, updated, deleted, matter_opened, matter_pended, matter_closed
      GET /webhooks/{id}.json               Webhook: id, url, fields, model, status (pending, enabled, suspended), events, expires_at
      PATCH /webhooks/{id}.json             the same fields (a new expires_at renews it)
      DELETE /webhooks/{id}.json            the subscription (not data); call() allows it for this path alone
  - Webhooks, https://docs.developers.clio.com/guides/clio-manage/webhooks/ and the FAQ https://docs.developers.clio.com/faq/ (read
    10/03/2026 through a page reader that summarises, so each sentence is to be read again on the page when the firm connects): POST
    /api/v4/webhooks with url, model, events (and fields, expires_at). Clio then POSTs the url with an X-Hook-Secret header (the signing
    secret) and data.webhook_id in the body; the endpoint answers 200 with the same X-Hook-Secret in its answer (or the subscription is
    activated later with PUT /api/v4/webhooks/{id}/activate and the secret in that header). Until then the webhook is "pending", then
    "enabled". Every delivery carries X-Hook-Signature: an HMAC-SHA256 "of the raw request body, keyed with the secret from the
    handshake" (the page says nothing of the signature's encoding, so hex or base64 are both accepted); the secret is not sent again.
    The body is {"data": {"id", "etag"[, the fields asked for]}, "meta": {"event", "webhook_id"}}: no event id and no time (so a
    replay is recognised by the body's own digest). "Webhooks last 3 days by default and 31 days at most, so extend expires_at before it
    lapses" (PATCH). Anything but 2xx or 3xx, or no answer "in time", is retried "with exponential backoff" (no count or timeout is
    published); "a webhook that keeps failing is eventually suspended"; 410 Gone disables at once. The app needs the model's scope and
    the "webhooks" scope. Not in the pages read: a limit on webhooks per app, whether the fields parameter takes a nested matter{id}
    for a document (the general Fields page allows one level), and whether a plain "updated" on a matter fires when its status or
    responsible attorney changes (the matter_opened, matter_pended and matter_closed events are named). Check on the firm's account.

Not verified (no Clio account was used; the tests run against a simulated
Clio): how Clio draws an all-day entry whose end is the same day (we send
00:00 to 23:59 on the deadline's date in the firm's zone); whether
GET /documents.json?matter_id= lists documents in the matter's sub-folders
(the reference says it filters "to include only the Document records with
the matching property"); the exact error body of a refused refresh. Check
on the firm's account with `python -m connectors.check clio` before the
first nightly run.

Where things are kept (the firm's data folder, never git): data/clio/
settings.json (switched on, the client id, the redirect URI, the mappings,
who changed them), secrets.enc (the client secret and the tokens, encrypted
with Fernet; the key is CLIO_TOKEN_KEY from the server's environment, else
vault.key beside it, readable only by the account the app runs as) and
state.json (which matter is which case, what was sent, the last sync, the
errors). CLIO_CLIENT_ID and CLIO_CLIENT_SECRET in the environment win over
what the Settings page saved.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

import clock
import oslock  # the product's one lock module (fcntl.flock, msvcrt.locking on Windows)
import events
import firmsecrets  # the one reader of keys and secrets, by name (src/firmsecrets.py)

from . import clio_sent, sync
from .base import RemoteClient, RemoteDoc

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
# Clio's US region (Regions page). I485_CLIO_BASE exists for the tests' simulated Clio only.
BASE = "https://app.clio.com"
API = "/api/v4"
AUTHORIZE, TOKEN, DEAUTHORIZE = "/oauth/authorize", "/oauth/token", "/oauth/deauthorize"
API_VERSION = "4.0.13"  # the default since 2026-01-06 (API changelog); pinned with X-API-VERSION
PER_MINUTE = 50  # "50 requests per minute during peak usage hours", per access token (Rate Limits page)
PAGE = 200  # "Index actions are limited to 200 results per request" (Pagination page)
TRIES = 6  # a 429 waits Retry-After and tries again, this many times at most
REFRESH_EARLY = 24 * 3600  # seconds before the 30-day token runs out that it is renewed
WEBHOOK_PATH = re.compile(r"/webhooks/\d+\.json")  # the one thing call() may delete: a webhook subscription this product made (Stop), never a record of the firm's
EXTERNAL = "i485_pipeline"  # the external property on every document and calendar entry we write (at most 5 per record)
SOURCE = "clio"  # documents.SOURCES; sync.local_id's prefix "cl"
QUESTIONNAIRES = {"i485": "Green card (I-485)", "n400": "Citizenship (N-400)"}  # portal/bank.py: the profile's "filing"
MATTER_FIELDS = ("id,display_number,description,status,updated_at,client{id,name,first_name,last_name,primary_email_address,primary_phone_number},"
                 "practice_area{id,name},responsible_attorney{id,name}")
DOC_FIELDS = "id,name,filename,content_type,size,updated_at,latest_document_version{id},external_properties{name,value}"
UPLOAD_FIELDS = "id,latest_document_version{uuid,put_url,put_headers}"
_lock = threading.Lock()  # one writer of state.json at a time (a sync thread and the Settings page)
CONNECTION_KEYS = ("access_token", "refresh_token", "expires_at", "webhook_secrets", "webhook_candidates")  # what the vault holds because Clio is connected (Disconnect forgets these)
_vault_lock = threading.Lock()  # and of secrets.enc


class ClioError(RuntimeError):
    """Clio refused or failed; the message is for the attorney's screen (no codes beyond the HTTP status)."""


class NotReady(ClioError):
    """Not switched on, not set up, or not connected: the sync does nothing and says why."""


class Stop(ClioError):
    """The whole sync stops here (Clio refused to renew the connection, or kept asking to slow down): every other case
    would meet the same answer. Any other ClioError is one case's problem, recorded against it, and the sync goes on."""


# What staff read when Clio refuses a call: the words, never the API path (that goes to the server's log).
REASONS = {400: "Clio didn't accept the request", 403: "no access (the connected Clio user, or the Clio app's permissions, don't allow it)",
           404: "it isn't in Clio, or the connected Clio user can't see it", 409: "it changed in Clio meanwhile",
           412: "it changed in Clio meanwhile", 410: "Clio no longer offers this version of its connection (the provider updates it)",
           422: "Clio didn't accept what was sent"}
NOUNS = {"tasks": "a task", "webhooks": "the webhook subscription", "matters": "the matters", "contacts": "a client's contact details", "documents": "a document", "notes": "a note",
         "calendar_entries": "a calendar entry", "calendars": "the calendars", "users": "the users", "practice_areas": "the practice areas",
         "custom_fields": "the contact fields"}
VERBS = {"GET": "reading", "POST": "adding", "PATCH": "updating", "DELETE": "stopping"}


def _words(method: str, path: str) -> str:
    """'GET /matters.json?...' -> 'reading the matters': what a call was doing, for a person."""
    resource = re.sub(r"\.json$", "", (path.split("/api/v4/", 1)[-1].lstrip("/").split("?")[0].split("/") or [""])[0])
    return f"{VERBS.get(method.upper(), 'changing')} {NOUNS.get(resource, 'a record')}"


def _log(text: str) -> None:
    """The technical detail of a refusal (method, path, status, Clio's message): the server's console, never the screen."""
    print(f"Clio: {text}", file=sys.stderr)


def base_url() -> str:
    return (os.environ.get("I485_CLIO_BASE") or BASE).rstrip("/")


# -- files: settings, secrets, state (data/clio) -----------------------------------------------------------------------


def folder(data_root: Path | None = None) -> Path:
    return Path(data_root or DATA) / "clio"


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


DEFAULT_SETTINGS: dict[str, Any] = {
    "on": False,
    "client_id": "",
    "redirect_uri": None,       # the review app's https address + /auth/callback, as registered with the Clio app (else the staff sign-in's)
    "practice_areas": {},       # Clio practice area id -> {"name", "questionnaire": "i485"|"n400", "office": office id or ""}: only these are read
    "attorney_offices": {},     # Clio user id -> {"name", "office": office id}: the responsible attorney's office
    "language_field": None,     # a Contact custom field (id) holding the client's language
    "calendar": None,           # the Clio calendar (id) deadlines go on; else the connected user's default calendar
    "uploads_hourly": False,    # "Read Clio uploads within the hour": a document or matter event from Clio's webhook starts a sync of that matter (clio_hooks.py)
}


def settings(data_root: Path | None = None) -> dict[str, Any]:
    return DEFAULT_SETTINGS | (_read(folder(data_root) / "settings.json", {}) or {})


def state(data_root: Path | None = None) -> dict[str, Any]:
    return _read(folder(data_root) / "state.json", {}) or {}


class Busy(ClioError):
    """Another process (or thread) holds the lock: for the Clio step, "a Clio sync is running"; for a short write, it was not free in time."""


class FileLock:
    """A lock between processes and threads (the review app's threads, the overnight run, the job worker's "clio" job) made with the product's one lock
    module, src/oslock.py (the operating system's own file lock: fcntl.flock on Linux, msvcrt.locking on Windows). The system keeps it: a held lock cannot
    be taken from its owner, a lock whose owner died (a crash, a kill, a reboot) is released by the system at once, so there is no age to guess, no
    process number to check and no stale file to clear. wait: how long to try (0: once); Busy when it was not free in time. Leaving a lock you do not hold
    does nothing. The lock file is never deleted (deleting a lock file others may be opening is how two owners happen)."""

    def __init__(self, path: Path, wait: float = 10.0):
        self.path, self.wait = Path(path), wait
        self._fd: int | None = None

    def __enter__(self):
        if self._fd is not None:
            raise RuntimeError("this lock is already held by this object")
        fd = oslock.open_lock_file(self.path)
        end = time.monotonic() + self.wait
        while not oslock.try_lock(fd):
            if time.monotonic() >= end:
                os.close(fd)
                raise Busy("Another Clio sync is running: try again in a few minutes.")
            time.sleep(0.02 if self.wait <= 30 else 5)
        self._fd = fd
        if os.name != "nt":  # who holds it, for a person looking (never read back by the product)
            try:
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()} {clock.stamp()}\n".encode())
            except OSError:
                pass
        return self

    def __exit__(self, *exc):
        fd, self._fd = self._fd, None
        if fd is None:  # not held by this object: nothing to release, and nobody else's lock is touched
            return
        try:
            oslock.unlock(fd)
        finally:
            os.close(fd)


def step_lock(data_root: Path | None, wait: float = 0.0) -> FileLock:
    """One Clio step at a time across processes (the overnight run's, the hourly read, Send now, Sync now): two would each keep Clio's 50 requests a
    minute for themselves and write the same state and client folders. A step that finds it held waits `wait` seconds, else says so."""
    return FileLock(folder(data_root) / "step.lock", wait=wait)


def _short_lock(data_root: Path | None, name: str) -> FileLock:
    """The brief lock around one read-modify-write of one of the files in data/clio (the review app and the overnight run both write them)."""
    return FileLock(folder(data_root) / f"{name}.lock", wait=15.0)


_paces: dict[str, "Pace"] = {}


def shared_pace(data_root: Path | None = None) -> "Pace":
    """The one request queue of this process for this firm's Clio: every operation (Subscribe, Check, renewing, the hourly read, Send now, the sync)
    waits in it, so they never add up to more than Clio's 50 a minute from here. (The overnight run is another process: the step lock keeps the two
    from running together.)"""
    key = str(Path(data_root or DATA).resolve())
    with _lock:
        if key not in _paces:
            _paces[key] = Pace()
        return _paces[key]


def _update_state(data_root: Path | None, change) -> dict[str, Any]:
    with _lock, _short_lock(data_root, "state"):
        s = state(data_root)
        change(s)
        _write(folder(data_root) / "state.json", s)
        return s


def _error(data_root: Path | None, what: str, case: str | None = None) -> None:
    """A problem for Settings, Connections ("Recent problems"): the last 20, each with the case it was about, if any."""
    def add(s):
        s["errors"] = (s.get("errors") or [])[-19:] + [{"at": clock.stamp(), "what": what, **({"case": case} if case else {})}]
    _update_state(data_root, add)


class Vault:
    """The client secret and the tokens, encrypted at rest (Fernet: AES-128-CBC with an HMAC). The key comes from
    CLIO_TOKEN_KEY in the server's environment, else vault.key beside the file, created once with owner-only access:
    a copy of the secrets file alone (a backup, a misplaced folder) then shows nothing; whoever holds the whole data
    folder holds the key too, which is why the environment key is the better choice for a hosted server."""

    def __init__(self, data_root: Path | None = None, env: dict[str, str] | None = None):
        self.dir = folder(data_root)
        self.path = self.dir / "secrets.enc"
        self.env = os.environ if env is None else env

    def _fernet(self):
        import firmsecrets

        try:  # the key (or keys, the first encrypting) is the environment's, else the key file beside the vault, made once owner-only: src/firmsecrets.py
            return firmsecrets.vault_cipher(self.dir, self.env)
        except (ValueError, TypeError):  # "Fernet key must be 32 url-safe base64-encoded bytes": not for a person
            raise NotReady("The server's encryption key for the Clio connection isn't a valid key: your IT sets it again (the setup guide), "
                           "then an attorney connects to Clio again.") from None

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        from cryptography.fernet import InvalidToken

        fernet = self._fernet()
        try:
            return json.loads(fernet.decrypt(self.path.read_bytes()))
        except (InvalidToken, ValueError):
            raise NotReady("The saved Clio connection can't be read on this server (its key changed): connect again from Settings.") from None

    def write(self, data: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the vault is owner-only like its key
        with os.fdopen(fd, "wb") as f:
            f.write(self._fernet().encrypt(json.dumps(data).encode()))
        os.replace(tmp, self.path)

    def rotate_key(self) -> int:
        """Every saved secret written again under a new key (secrets.enc is read with every key listed and written with the first). With the key file: a new key is made
        and put first, the vault is written again, then the old keys are dropped from the file. With CLIO_TOKEN_KEY: the new key must already be listed first and the old one
        after it; remove the old one once this has run. Returns how many saved values were written again."""
        import secretbox
        from cryptography.fernet import Fernet

        path = self.dir / "vault.key"
        in_env = firmsecrets.in_environment("clio.vault_key", self.env)
        if not in_env and not path.exists():
            raise NotReady("There is no key file beside the vault and CLIO_TOKEN_KEY isn't set here: run this with the environment the review app starts with. Nothing was changed.")
        with _vault_lock, self.lock():
            if not in_env:
                old = secretbox.keys(secretbox.key_file(path))
                new = Fernet.generate_key()
                secretbox.write_key_file(path, [new] + old)  # both read while the vault is written again
            try:
                data = self.read()
                self.write(data)
            except BaseException:
                if not in_env:
                    secretbox.write_key_file(path, old)  # nothing was written: the old key alone, as it was
                raise
            if not in_env:
                secretbox.write_key_file(path, [new])
            return len(data)

    def lock(self) -> "FileLock":
        """The brief lock around a read-modify-write of the vault, between processes (the review app and the overnight run both write it)."""
        return FileLock(self.dir / "vault.lock", wait=15.0)

    def update(self, **changes: Any) -> dict[str, Any]:
        with _vault_lock, self.lock():  # a sync renewing the token while the attorney saves a new secret: neither is lost
            data = self.read() | changes
            self.write({k: v for k, v in data.items() if v is not None})
            return data


def credentials(data_root: Path | None = None, env: dict[str, str] | None = None) -> tuple[str, str]:
    """(client id, client secret): the environment's, else what the Settings page saved (the secret is read by name, src/firmsecrets.py)."""
    import firmsecrets

    env = os.environ if env is None else env
    cfg = settings(data_root)
    client_id = env.get("CLIO_CLIENT_ID") or cfg.get("client_id") or ""
    return client_id, firmsecrets.get("clio.client_secret", env=env, data_root=data_root, strict=True) or ""


def redirect_uri(data_root: Path | None = None) -> str | None:
    """The address Clio sends the attorney back to: the Connections setting, else the staff sign-in's (connectors.json).
    Never built from a request's Host header."""
    own = settings(data_root).get("redirect_uri")
    if own:
        return own
    from . import signin

    return signin.config().get("redirect_uri")


# -- the settings the attorney changes (Settings, Connections) ----------------------------------------------------------

_REDIRECT = re.compile(r"(https://[^\s/?#]+|http://(127\.0\.0\.1|localhost)(:\d+)?)(/[^\s?#]*)?/auth/callback")


def save_settings(data_root: Path | None, values: dict[str, Any], who: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Checks and records the Connections values: switched on, the app's client id (and secret, into the vault), the
    redirect address, the mappings. Who and when are kept, with the earlier values."""
    if not str(who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    import offices

    office_ids = {o["id"] for o in offices.offices()}
    path = folder(data_root) / "settings.json"
    with _lock:
        old = _read(path, {}) or {}
        new = dict(old)
        for key, raw in values.items():
            if key in ("on", "uploads_hourly"):
                new[key] = bool(raw)
            elif key == "client_id":
                text = str(raw or "").strip()
                if text and not re.fullmatch(r"[A-Za-z0-9._~-]{8,200}", text):
                    raise ValueError("The Clio app's client id: copy it from the app's page in Clio's developer portal (letters and digits).")
                new["client_id"] = text
            elif key == "client_secret":
                text = str(raw or "").strip()
                if text:  # blank keeps the saved one; it is never shown back
                    if not re.fullmatch(r"[A-Za-z0-9._~-]{8,200}", text):
                        raise ValueError("The Clio app's client secret: copy it from the app's page in Clio's developer portal.")
                    Vault(data_root, env).update(client_secret=text)
                    firmsecrets.record("clio.client_secret", who, "set", data_root=data_root)  # that it was saved, by whom, when: never the value
            elif key == "redirect_uri":
                text = str(raw or "").strip() or None
                if text and not _REDIRECT.fullmatch(text):
                    raise ValueError("The review app's address for Clio: its https address followed by /auth/callback, exactly as entered "
                                     "in the Clio app's redirect URIs (for example https://review.firmname.com/auth/callback).")
                new["redirect_uri"] = text
            elif key == "practice_areas":
                rows = {}
                for pid, row in (raw or {}).items():
                    row = row or {}
                    if not row.get("questionnaire"):
                        continue  # not an immigration practice area: its matters stay in Clio only
                    if row["questionnaire"] not in QUESTIONNAIRES:
                        raise ValueError("Choose the questionnaire for each practice area from the list.")
                    if row.get("office") and row["office"] not in office_ids:
                        raise ValueError("Choose each practice area's office from the list.")
                    rows[str(pid)] = {"name": str(row.get("name") or "")[:120], "questionnaire": row["questionnaire"], "office": row.get("office") or ""}
                new["practice_areas"] = rows
            elif key == "attorney_offices":
                rows = {}
                for uid, row in (raw or {}).items():
                    row = row or {}
                    if not row.get("office"):
                        continue
                    if row["office"] not in office_ids:
                        raise ValueError("Choose each attorney's office from the list.")
                    rows[str(uid)] = {"name": str(row.get("name") or "")[:120], "office": row["office"]}
                new["attorney_offices"] = rows
            elif key in ("language_field", "calendar"):
                text = str(raw or "").strip()
                if text and not text.isdigit():
                    raise ValueError("Choose from the list Clio gave.")
                new[key] = text or None
            else:
                raise ValueError(f"Not a setting here: {key}.")
        history = (old.get("history") or []) + ([{k: v for k, v in old.items() if k != "history"}] if old else [])
        new |= {"updated_by": who.strip(), "updated_at": clock.stamp(), "history": history[-20:]}
        _write(path, new)
    events.record("settings", "changed", "Changed the Clio connection's settings", home=Path(data_root or DATA), who=who.strip())
    return settings(data_root)


# -- the queue every request waits in -------------------------------------------------------------------------------


class Pace:
    """At most `limit` requests in any 60 seconds (Clio's 50 a minute at peak, per token). An answer's X-RateLimit-Limit
    sets the limit (higher off-peak); X-RateLimit-Remaining 0 holds every request until X-RateLimit-Reset; a 429 waits
    Retry-After. Tests give it their own clock and sleep."""

    def __init__(self, limit: int = PER_MINUTE, monotonic=time.monotonic, sleep=time.sleep, wall=time.time):
        self.limit, self.monotonic, self.sleep, self.wall = limit, monotonic, sleep, wall
        self.sent: list[float] = []
        self.hold_until = 0.0  # a wall-clock time (X-RateLimit-Reset is a unix time)
        self.waited = 0.0

    def _nap(self, seconds: float) -> None:
        if seconds > 0:
            self.waited += seconds
            self.sleep(seconds)

    def wait(self) -> None:
        self._nap(self.hold_until - self.wall())
        now = self.monotonic()
        self.sent = [t for t in self.sent if t > now - 60]
        if len(self.sent) >= self.limit:
            self._nap(self.sent[0] + 60 - now)
            now = self.monotonic()
            self.sent = [t for t in self.sent if t > now - 60]
        self.sent.append(self.monotonic())

    def saw(self, headers: httpx.Headers) -> None:
        try:
            if headers.get("X-RateLimit-Limit"):
                self.limit = max(1, int(headers["X-RateLimit-Limit"]))
            if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
                self.hold_until = float(headers["X-RateLimit-Reset"])
        except ValueError:
            pass

    def back_off(self, headers: httpx.Headers, attempt: int) -> None:
        try:
            seconds = float(headers.get("Retry-After") or 0)
        except ValueError:
            seconds = 0
        self._nap(seconds or min(60, 5 * 2 ** attempt))


# -- OAuth (the review app's /auth/start and /auth/callback carry the state: review/server.py) ----------------------------


def authorize_url(data_root: Path | None, state_value: str, env: dict[str, str] | None = None) -> str:
    """Where "Connect to Clio" sends the attorney (Clio's consent screen); NotReady says what is missing."""
    client_id, secret = credentials(data_root, env)
    if not client_id or not secret:
        raise NotReady("Enter the Clio app's client id and client secret first, and save.")
    uri = redirect_uri(data_root)
    if not uri:
        raise NotReady("Enter the review app's address for Clio first (Connections), exactly as in the Clio app's redirect URIs.")
    return base_url() + AUTHORIZE + "?" + urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": uri,
                                                       "state": state_value, "redirect_on_decline": "true"})


def _token_answer(r: httpx.Response, what: str) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
        _log(f"POST {TOKEN} ({what}) -> {r.status_code} {body.get('error') if isinstance(body, dict) else ''}")
        raise ClioError(f"Clio refused {what}.")
    return body


def connect(data_root: Path | None, code: str, who: str, env: dict[str, str] | None = None,
            transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Clio sent the attorney back with a code: it becomes the tokens (kept in the vault), and who_am_i says whose."""
    client_id, secret = credentials(data_root, env)
    uri = redirect_uri(data_root)
    if not (client_id and secret and uri):
        raise NotReady("The Clio app isn't set up here any more.")
    http = httpx.Client(transport=transport, timeout=30)
    body = _token_answer(http.post(base_url() + TOKEN, data={"client_id": client_id, "client_secret": secret, "grant_type": "authorization_code",
                                                             "code": code, "redirect_uri": uri}), "the connection")
    if not body.get("refresh_token"):
        raise ClioError("Clio didn't give a refresh token: connect again.")
    expires = clock.utcnow().timestamp() + float(body.get("expires_in") or 2592000)
    Vault(data_root, env).update(access_token=body["access_token"], refresh_token=body["refresh_token"], expires_at=expires)
    me = Clio(data_root, transport=transport, env=env).call("GET", "/users/who_am_i.json",
                                                            params={"fields": "id,name,email,default_calendar_id"}).json().get("data") or {}

    def record(s):
        s["connected"] = {"user": {k: me.get(k) for k in ("id", "name", "email", "default_calendar_id")}, "by": who, "at": clock.stamp()}
        s.pop("refresh_failed", None)
    _update_state(data_root, record)
    events.record("settings", "connected", "Connected the firm's Clio account", home=Path(data_root or DATA), who=who)
    return me


def disconnect(data_root: Path | None, who: str, env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None) -> None:
    """Asks Clio to withdraw the access token (deauthorize) and forgets both tokens here; the settings stay. Nothing in
    Clio or here is deleted."""
    vault = Vault(data_root, env)
    tokens = vault.read()
    from . import clio_hooks

    if clio_hooks.load(data_root)["hooks"]:  # our webhook subscriptions first, while the token still works (Clio would only retry and suspend them)
        try:
            clio_hooks.stop(data_root, who, transport=transport, env=env)
        except (ClioError, httpx.HTTPError):
            clio_hooks.forget(data_root)
    if tokens.get("access_token"):
        try:
            httpx.Client(transport=transport, timeout=30).post(base_url() + DEAUTHORIZE, data={"token": tokens["access_token"]},
                                                               headers={"Authorization": f"Bearer {tokens['access_token']}"})
        except httpx.HTTPError:
            pass  # forgotten here either way; the attorney can also remove the app in Clio
    with _vault_lock, vault.lock():
        vault.write({k: v for k, v in vault.read().items() if k not in CONNECTION_KEYS})  # read again: stopping the webhooks changed the vault; the app's own secret and the firm's other secrets stay

    def record(s):
        s["disconnected"] = {"by": who, "at": clock.stamp(), "was": (s.get("connected") or {}).get("user")}
        s.pop("connected", None)
    _update_state(data_root, record)
    events.record("settings", "disconnected", "Disconnected the firm's Clio account", home=Path(data_root or DATA), who=who)


# -- the API ---------------------------------------------------------------------------------------------------------


class Clio:
    """One conversation with Clio: the token renewed before it runs out (or once on a 401), every request through the
    Pace queue, a 429 waited out, cursor paging. As a document source (sync.mirror) it lists the mapped matters as
    clients and their documents; for results it uploads files and posts notes and calendar entries, called only by
    sync_out (which holds back protected cases and drafts)."""

    name = SOURCE

    def __init__(self, data_root: Path | None = None, transport: httpx.BaseTransport | None = None, env: dict[str, str] | None = None,
                 pace: Pace | None = None):
        self.data_root = data_root
        self.env = os.environ if env is None else env
        self.cfg = settings(data_root)
        self.vault = Vault(data_root, self.env)
        self.http = httpx.Client(transport=transport, timeout=120)
        self.pace = pace or shared_pace(data_root)
        self.base = base_url()
        self.calls = 0
        self._clients: list[RemoteClient] | None = None
        self._ours: set[str] = set()
        self._users: list[dict[str, Any]] | None = None

    # -- tokens ------------------------------------------------------------------

    def _tokens(self) -> dict[str, Any]:
        tokens = self.vault.read()
        if not tokens.get("access_token"):
            raise NotReady("Not connected to Clio: an attorney connects it on the Settings page (Connections).")
        return tokens

    def refresh(self) -> str:
        """A new access token from the refresh token; a refusal is recorded for Keeping current and stops the sync."""
        tokens = self._tokens()
        client_id, secret = credentials(self.data_root, self.env)
        r = self.http.post(self.base + TOKEN, data={"client_id": client_id, "client_secret": secret, "grant_type": "refresh_token",
                                                    "refresh_token": tokens.get("refresh_token") or ""})
        try:
            body = _token_answer(r, "to renew the connection")
        except ClioError as exc:
            failed = {"at": clock.stamp(), "why": str(exc)}
            _update_state(self.data_root, lambda s: s.__setitem__("refresh_failed", failed))
            raise Stop("Clio refused to renew the connection: an attorney connects it again on the Settings page (Connections).") from None
        expires = clock.utcnow().timestamp() + float(body.get("expires_in") or 2592000)
        self.vault.update(access_token=body["access_token"], refresh_token=body.get("refresh_token") or tokens.get("refresh_token"), expires_at=expires)
        _update_state(self.data_root, lambda s: (s.pop("refresh_failed", None), s.__setitem__("refreshed_at", clock.stamp())))
        return body["access_token"]

    def token(self) -> str:
        tokens = self._tokens()
        if float(tokens.get("expires_at") or 0) - clock.utcnow().timestamp() < REFRESH_EARLY:
            return self.refresh()
        return tokens["access_token"]

    # -- requests ----------------------------------------------------------------

    def call(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None, what: str | None = None,
             unsubscribe: bool = False, headers: dict[str, str] | None = None) -> httpx.Response:
        """One request (never a DELETE, but for `unsubscribe`: our own webhook subscription), redirects not followed: a 303 is returned to the
        caller (a download's signed link). A refusal says `what` was being done ("the upload of the filing packet for ...") and why, in
        words; the method, path and Clio's own message go to the server's log."""
        if method.upper() == "DELETE" and not (unsubscribe and WEBHOOK_PATH.fullmatch(path)):  # docs/integrations.md: nothing is ever deleted on either side
            raise ClioError("Deleting in Clio is never done from here.")
        url = path if path.startswith("http") else self.base + API + path
        renewed = False
        for attempt in range(TRIES):
            self.pace.wait()
            self.calls += 1
            r = self.http.request(method, url, params=params if not path.startswith("http") else None, json=json, follow_redirects=False,
                                  headers={"Authorization": f"Bearer {self.token()}", "X-API-VERSION": API_VERSION, "Accept": "application/json"} | (headers or {}))
            self.pace.saw(r.headers)
            if r.status_code == 429:
                self.pace.back_off(r.headers, attempt)
                continue
            if r.status_code == 401 and not renewed:
                renewed = True
                self.refresh()
                continue
            if r.status_code >= 400:
                _log(f"{method} {url.split('?')[0]} -> {r.status_code}{_why(r)}")
                doing = what or _words(method, path)
                if r.status_code >= 500:
                    raise ClioError(f"Clio had a problem with {doing}: it is tried again at the next sync.")
                raise ClioError(f"Clio refused {doing}: {REASONS.get(r.status_code, 'Clio said no')}.")
            return r
        raise Stop("Clio kept asking to slow down: the rest waits for the next sync.")

    def pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Every record of an index (cursor paging: order=id(asc), meta.paging.next until it is absent)."""
        out: list[dict[str, Any]] = []
        body = self.call("GET", path, params={**params, "order": "id(asc)", "limit": PAGE}).json()
        while True:
            out += body.get("data") or []
            nxt = ((body.get("meta") or {}).get("paging") or {}).get("next")
            if not nxt:
                return out
            body = self.call("GET", nxt).json()

    # -- lists the attorney maps (Settings, Connections) ------------------------------

    def options(self) -> dict[str, Any]:
        return {"practice_areas": [{"id": str(p["id"]), "name": p.get("name") or ""} for p in self.pages("/practice_areas.json", {"fields": "id,name"})],
                "users": [{"id": str(u["id"]), "name": u.get("name") or "", "attorney": u.get("subscription_type") == "Attorney"}
                          for u in self.pages("/users.json", {"fields": "id,name,enabled,subscription_type"}) if u.get("enabled") is not False],
                "contact_fields": [{"id": str(f["id"]), "name": f.get("name") or ""}
                                   for f in self.pages("/custom_fields.json", {"parent_type": "contact", "fields": "id,name,field_type"})
                                   if f.get("field_type") in ("text_line", "picklist", "text_area")],
                "calendars": [{"id": str(c["id"]), "name": c.get("name") or "", "type": c.get("type")}
                              for c in self.pages("/calendars.json", {"writeable": "true", "fields": "id,name,type"})],
                "fetched_at": clock.stamp()}

    # -- source ------------------------------------------------------------------

    def clients(self) -> list[RemoteClient]:
        """The open and pending matters in the mapped practice areas, each as a client (its Clio client contact)."""
        if self._clients is not None:
            return self._clients
        areas = self.cfg.get("practice_areas") or {}
        out = []
        for pid in areas:
            for m in self.pages("/matters.json", {"practice_area_id": pid, "status": "open,pending", "fields": MATTER_FIELDS}):
                found = self._remote(m)
                if found is not None:
                    out.append(found)
        self._clients = out
        return out

    def _remote(self, m: dict[str, Any]) -> RemoteClient | None:
        """A matter as a client (its Clio client contact, its language from the contact field the firm named); None when the connected user
        can't see the client."""
        c = m.get("client") or {}
        if c.get("redacted"):
            return None  # the connected user can't see this client: it stays in Clio
        lang_field = self.cfg.get("language_field")
        name = c.get("name") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or m.get("display_number") or str(m["id"])
        language = None
        if lang_field and c.get("id"):
            values = (self.call("GET", f"/contacts/{c['id']}.json", params={"custom_field_ids[]": lang_field,
                                                                            "fields": "id,custom_field_values{id,value,field_name}"}).json()
                      .get("data") or {}).get("custom_field_values") or []
            language = next((str(v.get("value")) for v in values if v.get("value")), None)
        return RemoteClient(id=str(m["id"]), name=name, phone=c.get("primary_phone_number") or "", email=c.get("primary_email_address") or "",
                            language=language or "", raw=m)

    def matter(self, matter_id: str) -> RemoteClient | None:
        """One matter as a client, as it is in Clio now (a Clio webhook named it); None when it isn't open or pending, isn't in a practice
        area the attorney mapped, or the connected user can't see its client. One request (and the contact's, when a language field is set)."""
        m = (self.call("GET", f"/matters/{int(matter_id)}.json", params={"fields": MATTER_FIELDS}, what="reading a matter").json().get("data") or {})
        if not m or str(m.get("status") or "").lower() not in ("open", "pending") or str((m.get("practice_area") or {}).get("id")) not in (self.cfg.get("practice_areas") or {}):
            return None
        return self._remote(m)

    def users(self) -> list[dict[str, Any]]:
        """Clio's users with their e-mail (to give a task to the person it names), once per sync."""
        if self._users is None:
            self._users = self.pages("/users.json", {"fields": "id,name,email,enabled"})
        return self._users

    def assignee(self, email: str | None, state_user: dict[str, Any] | None) -> tuple[str, str]:
        """(the Clio user id a task goes to, "matched" or "fallback"): the enabled user whose e-mail is the person's, else the connected user (Clio
        makes no task without an assignee)."""
        email = str(email or "").strip().lower()
        if email:
            for u in self.users():
                if u.get("enabled") is not False and str(u.get("email") or "").strip().lower() == email:
                    return str(u["id"]), "matched"
        if not (state_user or {}).get("id"):
            raise ClioError("Clio makes no task without someone to give it to, and no connected Clio user is on record: connect again from Settings.")
        return str(state_user["id"]), "fallback"

    def documents(self, client: RemoteClient) -> list[RemoteDoc]:
        docs = []
        for d in self.pages("/documents.json", {"matter_id": client.id, "fields": DOC_FIELDS}):
            if str(d["id"]) in self._ours or any((p or {}).get("name") == EXTERNAL for p in d.get("external_properties") or []):
                continue  # what we put there (the packet, the bundle, a mailing record) never comes back in as a document
            version = (d.get("latest_document_version") or {}).get("id")
            docs.append(RemoteDoc(id=str(d["id"]), name=d.get("filename") or d.get("name") or str(d["id"]), mime=d.get("content_type") or "",
                                  size=d.get("size"), modified=d.get("updated_at"), checksum=str(version or ""), raw=d))
        return docs

    def download(self, client: RemoteClient, doc: RemoteDoc) -> bytes:
        r = self.call("GET", f"/documents/{doc.id}/download.json", what=f"the download of {client.name}'s document {doc.name}")
        if r.status_code == 303:  # a signed link elsewhere: fetched without Clio's token
            r = self.http.get(r.headers["Location"])
            if r.status_code != 200:
                _log(f"GET (signed download link) for document {doc.id} -> {r.status_code}")
                raise ClioError(f"The download of {client.name}'s document {doc.name} from Clio failed: it is tried again at the next sync.")
        return r.content

    # -- sink --------------------------------------------------------------------

    def upload(self, matter_id: str, data: bytes, name: str, kind: str, version_of: str | None = None, what: str | None = None) -> str:
        """A PDF onto the matter (or a new version of our earlier document): create, PUT to the signed URL, mark uploaded."""
        what = what or f"the upload of {name}"
        parent = {"id": int(version_of), "type": "Document"} if version_of else {"id": int(matter_id), "type": "Matter"}
        meta = {"name": name, "parent": parent, "content_type": "application/pdf"}
        if not version_of:
            meta["external_properties"] = [{"name": EXTERNAL, "value": kind}]
        created = (self.call("POST", "/documents.json", params={"fields": UPLOAD_FIELDS}, json={"data": meta}, what=what).json().get("data") or {})
        doc_id, version = str(created.get("id") or version_of), created.get("latest_document_version") or {}
        if not version.get("put_url") or not version.get("uuid"):
            _log(f"POST /documents.json gave no latest_document_version put_url/uuid for {name}")
            raise ClioError(f"Clio gave no place to upload to for {what}: it is tried again at the next sync.")
        headers = {h["name"]: h["value"] for h in version.get("put_headers") or [] if h.get("name")}
        put = self.http.put(version["put_url"], content=data, headers=headers)  # the signed URL carries its own credentials
        if put.status_code not in (200, 201, 204):
            _log(f"PUT (signed upload link) for document {doc_id} -> {put.status_code}")
            raise ClioError(f"Clio's storage refused {what}: it is tried again at the next sync.")
        for attempt in (1, 2):  # "It is possible for the verification to time out ... you will need to retry the request."
            try:
                self.call("PATCH", f"/documents/{doc_id}.json", params={"fields": "id,latest_document_version{fully_uploaded}"},
                          json={"data": {"uuid": version["uuid"], "fully_uploaded": True}}, what=what)
                break
            except ClioError:
                if attempt == 2:
                    raise
        self._ours.add(doc_id)
        return doc_id

    def note(self, matter_id: str, subject: str, detail: str, what: str | None = None) -> str:
        r = self.call("POST", "/notes.json", params={"fields": "id"},
                      json={"data": {"type": "Matter", "matter": {"id": int(matter_id)}, "subject": subject, "detail": detail,
                                     "date": clock.today().isoformat()}}, what=what)
        return str((r.json().get("data") or {}).get("id") or "")

    def calendar_entry(self, matter_id: str, calendar_id: str, when: date, summary: str, description: str, key: str,
                       entry_id: str | None = None, what: str | None = None) -> str:
        """An all-day entry on the deadline's date (in the firm's zone), tagged with our key; PATCH when it exists."""
        zone = clock.zone()
        data = {"summary": summary, "description": description, "all_day": True,
                "start_at": datetime.combine(when, dtime(0, 0), tzinfo=zone).isoformat(),
                "end_at": datetime.combine(when, dtime(23, 59), tzinfo=zone).isoformat()}
        if entry_id:
            self.call("PATCH", f"/calendar_entries/{entry_id}.json", params={"fields": "id"}, json={"data": data}, what=what)
            return entry_id
        data |= {"matter": {"id": int(matter_id)}, "calendar_owner": {"id": int(calendar_id)}, "external_properties": [{"name": EXTERNAL, "value": key}]}
        r = self.call("POST", "/calendar_entries.json", params={"fields": "id"}, json={"data": data}, what=what)
        return str((r.json().get("data") or {}).get("id") or "")

    def task(self, matter_id: str, assignee_id: str, name: str, description: str, due: date, what: str | None = None) -> str:
        """A Clio task on the matter (POST /tasks.json: a name, a description, one assignee, the day it is due). Nobody is e-mailed by Clio about it
        (notify_assignee false): the firm's own reminders (My work, the calendar feed) already tell the person."""
        r = self.call("POST", "/tasks.json", params={"fields": "id"},
                      json={"data": {"name": name, "description": description, "due_at": due.isoformat(), "matter": {"id": int(matter_id)},
                                     "assignee": {"id": int(assignee_id), "type": "User"}, "priority": "Normal", "status": "pending",
                                     "notify_assignee": False}}, what=what)
        return str((r.json().get("data") or {}).get("id") or "")

    def task_update(self, task_id: str, assignee_id: str, description: str, due: date, what: str | None = None) -> None:
        """A task whose person responsible or date changed here (PATCH /tasks/{id}.json: assignee, description, due_at)."""
        self.call("PATCH", f"/tasks/{int(task_id)}.json", params={"fields": "id"},
                  json={"data": {"assignee": {"id": int(assignee_id), "type": "User"}, "description": description, "due_at": due.isoformat()}}, what=what)

    def task_complete(self, task_id: str, what: str | None = None) -> None:
        self.call("PATCH", f"/tasks/{int(task_id)}.json", params={"fields": "id"}, json={"data": {"status": "complete"}}, what=what)

    def calendar_close(self, entry_id: str, summary: str, description: str, what: str | None = None) -> None:
        self.call("PATCH", f"/calendar_entries/{entry_id}.json", params={"fields": "id"}, json={"data": {"summary": summary, "description": description}},
                  what=what)

    # No publish(files, note), the other connectors' ResultSink call: given bare files it couldn't tell a protected case
    # (held_back) or a draft packet, so everything that goes out goes through sync_out, which checks both.


def _why(r: httpx.Response) -> str:
    try:
        err = (r.json() or {}).get("error") or {}
    except ValueError:
        return "."
    msg = err.get("message") if isinstance(err, dict) else None
    return f": {msg}." if msg else "."


# -- what a case sends, and what is held back --------------------------------------------------------------------------

PROTECTED = {"1367": "a protected case (8 U.S.C. 1367: VAWA, T or U)", "208.6": "an asylum or refugee case (8 CFR 208.6)"}


def held_back(out_dir: Path, case: str, st: dict[str, Any]) -> str | None:
    """Why nothing of this case goes to Clio, or None. A protected case is held back until the attorney allows it
    (Connections, "Send to Clio"): Clio's matter permissions are the firm's, not this app's. A case an attorney
    marked restricted on its page (src/restricted.py, Part 10.3) is held back the same way, with the same allowance;
    the sync has no signed-in person, so it asks the case's record, not who may open it."""
    if case in (st.get("allowed") or {}):
        return None
    try:
        import restricted

        if (restricted.record(out_dir)["marked"] or {}).get("on"):
            return "a case an attorney restricted"
    except Exception:  # noqa: BLE001 -- a record that can't be read may carry an attorney's mark: held back
        return "the case couldn't be read"
    try:
        import documents

        kind = documents.case_confidentiality(out_dir)
    except Exception:  # noqa: BLE001 -- a case that can't be read is held back, never sent by mistake
        return "the case couldn't be read"
    return PROTECTED.get(kind) if kind else None


def case_view(out_dir: Path) -> dict[str, Any]:
    """The stage, the deadlines and the tasks (src/journey.py), which deadlines a person marked done, and the case's end state (src/engagement.py).
    A deadline or a task a person set (src/deadlines_set.py, src/case_notes.py) is one row of that file: a task is marked "task", and one marked
    done leaves journey's open list, so it comes back from the done list to be closed in Clio ("Done: ...", complete) and not "No longer due"."""
    import journey

    j = journey.journey(out_dir)
    status = _read(out_dir / "status.json", {}) or {}
    done = dict((status.get("journey") or {}).get("done") or {})
    deadlines = [d for d in j["deadlines"] if not d.get("task")]
    tasks = [d | {"done": None} for d in j["deadlines"] if d.get("task")]
    for d in j.get("deadlines_done") or []:
        stamp = {"by": d.get("done_by"), "at": d.get("done_at")}
        if d.get("task"):
            tasks.append({"id": d["id"], "date": d["date"], "what": d["what"], "who_name": d.get("who_name"), "note": d.get("note"), "done": stamp})
        else:
            deadlines.append({"id": d["id"], "date": d["date"], "what": d["what"], "owner": "paralegal", "source": None, "who_name": d.get("who_name"),
                              "note": d.get("note")})
            done[d["id"]] = stamp
    try:
        import engagement

        end = engagement.end_info(out_dir)
    except Exception:  # noqa: BLE001 -- a case whose end can't be read sends no end note this time
        end = None
    return {"stage": j["stage_name"], "why": j.get("why") or "", "deadlines": deadlines, "done": done, "tasks": tasks, "end": end}


def _packets(out_dir: Path) -> list[tuple[str, Path, dict[str, Any]]]:
    """(key, pdf, manifest) for every filing packet built and not a draft: packet.json -> packet.pdf,
    packet_ead.json -> packet_ead.pdf (each filing's own manifest, packet.FILINGS)."""
    out = []
    for m in sorted(out_dir.glob("packet*.json")):
        if m.name.startswith("packet_choices") or m.name.endswith("_review_bundle.json"):
            continue
        manifest = _read(m, {}) or {}
        pdf = m.with_suffix(".pdf")
        if isinstance(manifest, dict) and manifest.get("built_at") and not manifest.get("draft") and pdf.exists():
            out.append((m.stem, pdf, manifest))
    return out


def _title(filing: str | None) -> str:
    try:
        import packet

        return str(packet.load_filing(filing).get("title") or filing or "filing")
    except Exception:  # noqa: BLE001 -- a filing without its own schema keeps its code as the name
        return str(filing or "filing")


def mailing_pdf(case_name: str, record: dict[str, Any]) -> bytes:
    """A one-page record of a mailing (status.json "filings"): what, when, how, where, the fee, who recorded it."""
    from pypdf import PdfWriter

    from fill.continuation import HEIGHT, MARGIN, _Page

    p = _Page()
    y = HEIGHT - 70
    p.text(MARGIN, y, "Mailing record", "F2", 14)
    y -= 22
    p.text(MARGIN, y, f"Client: {case_name}", "F3", 10)
    rows = [("Filing", record.get("title") or _title(record.get("filing"))), ("Mailed on", clock.us_date(record.get("mailed_on")) or "-"),
            ("How", record.get("carrier") or "-"), ("Tracking or receipt number", record.get("tracking") or record.get("receipt") or "-"),
            ("Mailed to", record.get("mail_to") or ("USCIS online account" if record.get("online") else "-")), ("Fee", record.get("fee") or "-"),
            ("Recorded by", f"{record.get('by') or '-'}, {clock.us_date(record.get('at')) or '-'}")]
    if record.get("override"):
        rows.append(("Mailed although a check failed, because", record["override"]))
    y -= 30
    for label, value in rows:
        p.text(MARGIN, y, label, "F3", 9)
        lines = [str(value)[i:i + 70] for i in range(0, len(str(value)), 70)] or ["-"]
        for line in lines:
            p.text(MARGIN + 190, y, line, "F2", 10)
            y -= 14
        y -= 4
    p.text(MARGIN, 50, "Recorded in the firm's review app and copied to Clio. Nothing here is a new filing.", "F3", 8)
    w = PdfWriter()
    w.add_page(p.to_page(w))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _summary(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


# -- the sync, both ways --------------------------------------------------------------------------------------------


def ready(data_root: Path | None = None, env: dict[str, str] | None = None) -> str | None:
    """Why the sync can't run (None when it can): switched off, the app not set up, or not connected."""
    cfg = settings(data_root)
    if not cfg.get("on"):
        return "switched off"
    try:
        client_id, secret = credentials(data_root, env)
        if not (client_id and secret):
            return "the Clio app's client id and secret aren't saved"
        if not Vault(data_root, env).read().get("refresh_token"):
            return "not connected"
    except NotReady as exc:
        return str(exc).rstrip(".")
    if not cfg.get("practice_areas"):
        return "no practice area is mapped to a questionnaire yet"
    return None


def _mark_ours(data_root: Path | None, client: Clio) -> None:
    """What we put in Clio (the packets, bundles and mailing records) never comes back in as one of the client's documents."""
    out = state(data_root).get("out") or {}
    client._ours |= {d["id"] for case in out.values() for d in (case.get("docs") or {}).values() if d.get("id")} | \
        {m for case in out.values() for m in (case.get("mailings") or {}).values()}


def sync_in(data_root: Path | None, clients_root: Path, portal_root: Path | None, client: Clio, out_root: Path | None = None) -> dict[str, Any]:
    """Matters -> case folders (their documents, only what is new or changed) and portal clients. out_root: the review app's case
    folders. A matter whose practice area names a VAWA, T, U or asylum case (restricted.kind_law) is restricted there the first time
    it is seen, before its portal client is made, and is never invited by anything (src/restricted.py protect_new)."""
    import restricted

    cfg = client.cfg
    _mark_ours(data_root, client)
    store = None
    if portal_root is not None:
        from portal.store import PortalStore

        store = PortalStore(portal_root)
    protected = []
    for rc in client.clients():  # the restriction record first, before the conflict check or anything else of the case (no step in between may open it)
        m = rc.raw
        area = cfg["practice_areas"].get(str((m.get("practice_area") or {}).get("id"))) or {}
        kind = str(area.get("name") or (m.get("practice_area") or {}).get("name") or "")
        found = restricted.kind_law(kind)
        lid = sync.local_id(rc, SOURCE)
        if found and out_root is not None and restricted.protect_new(Path(out_root) / lid, found, kind, "Came in from Clio, practice area", "the Clio sync"):
            protected.append(lid)
    held = _conflict_search(client, Path(clients_root), Path(out_root) if out_root is not None else Path(clients_root), store)
    report = sync.mirror(client, Path(clients_root), home=Path(data_root or DATA))
    added, cases = [], {}
    for rc in client.clients():
        cases[str(rc.id)] = _link(cfg, store, rc, added)

    def record(s):
        s.setdefault("matters", {}).update(cases)
    _update_state(data_root, record)
    files = sum(len(c["added"]) + len(c["updated"]) for c in report["clients"].values())
    return {"matters": len(cases), "new_clients": added, "files": files, "report": report, "protected": protected, "conflicts": held}


def _link(cfg: dict[str, Any], store, rc: RemoteClient, added: list[str]) -> dict[str, Any]:
    """One matter's entry for state.json "matters" (its case, name, number, questionnaire, office) and, when there is a portal, its portal client: made
    when new (every channel off: Clio holds no consent), and what the client's profile lacks filled in. What the office already set stays."""
    lid = sync.local_id(rc, SOURCE)
    m = rc.raw
    area = cfg["practice_areas"].get(str((m.get("practice_area") or {}).get("id"))) or {}
    # the office: the practice area's when the firm set one, else the responsible attorney's, else (left out) the client's state's
    office = area.get("office") or (cfg["attorney_offices"].get(str((m.get("responsible_attorney") or {}).get("id"))) or {}).get("office") or ""
    entry = {"case": lid, "name": rc.name, "number": m.get("display_number"), "questionnaire": area.get("questionnaire"), "office": office or None}
    if store is not None:
        from portal.bank import language_code

        language = language_code(rc.language) if rc.language else None
        try:
            store.profile(lid)
            known = True
        except LookupError:
            known = False
        if not known:
            # Clio holds no consent to be messaged: every channel stays off until the office records the client's consent
            store.add_client(lid, rc.name, phone=rc.phone, email=rc.email, language=language or "pt",
                             consent={"email": False, "sms": False, "whatsapp": False})
            added.append(lid)
        profile = store.profile(lid)
        fill = {k: v for k, v in (("phone", rc.phone), ("email", rc.email)) if v and not profile.get(k)}
        # what the office set on the client already (the questionnaire, the office) stays as it is
        store.update_profile(lid, **fill, source=SOURCE, clio_matter=str(rc.id), clio_matter_id=str(rc.id),  # the importer's key too: one meaning, two spellings
                             **({"filing": area["questionnaire"]} if area.get("questionnaire") and not profile.get("filing") else {}),
                             **({"office": office} if office and not profile.get("office") else {}))
    return entry


def _conflict_search(client: Clio, clients_root: Path, out_root: Path, store) -> list[str]:
    """Before a new matter's case is made: the conflict search for its client (src/conflicts.py), logged and recorded on the case as not yet
    decided, so no invitation goes until an attorney decides on the screen. A matter seen before (its folder, its portal client or its record
    exists) is left as it is. Returns the new cases recorded."""
    import conflicts

    held, first = [], True
    for rc in client.clients():
        lid = sync.local_id(rc, SOURCE)
        # seen before: its record, its mirrored documents (an earlier sync) or its case file (the restriction record alone, written just above, is not)
        known = (out_root / lid / conflicts.FILE).exists() or (clients_root / lid / "source").exists() or (out_root / lid / "fact_graph.json").exists()
        if not known and store is not None:
            try:
                store.profile(lid)
                known = True
            except LookupError:
                pass
        if known:
            continue
        try:
            conflicts.hold_new(out_root, lid, {"name": rc.name}, by="the Clio sync", purpose="sync", via="connector", refresh=first)
        except Exception as exc:  # noqa: BLE001 -- no case without its conflict check: nothing comes in this time
            raise ClioError(f"The conflict search could not run ({type(exc).__name__}), so nothing new was copied in from Clio; it is tried again at the next sync.") from exc
        first = False
        held.append(lid)
    return held


def sync_out(data_root: Path | None, out_root: Path, client: Clio, only: set[str] | None = None, by_hand: dict[str, str] | None = None) -> dict[str, Any]:
    """Each mapped case that has been processed: its packet(s), review bundle(s), mailing records, stage, deadlines, tasks and end state.
    only: just these cases (the case page's "Send now"; by_hand says who pressed it). What Clio refuses for a case is recorded against it
    (state.json "failed" and the case's own record, clio_sent.py) and cleared the next time it goes through."""
    st = state(data_root)
    calendar = client.cfg.get("calendar") or ((st.get("connected") or {}).get("user") or {}).get("default_calendar_id")
    counts = {"documents": 0, "notes": 0, "entries_new": 0, "entries_moved": 0, "entries_closed": 0, "tasks_new": 0, "tasks_moved": 0, "tasks_closed": 0, "held": 0, "failed": 0}
    held: dict[str, str] = dict(st.get("held") or {}) if only else {}
    failed: dict[str, Any] = dict(st.get("failed") or {})
    for mid, info in sorted((st.get("matters") or {}).items()):
        lid = info["case"]
        if only is not None and lid not in only:
            continue
        out_dir = Path(out_root) / lid
        if not (out_dir / "fact_graph.json").exists():
            continue  # not processed yet: nothing to send
        why = held_back(out_dir, lid, st)
        if why:
            held[lid] = why
            counts["held"] += 1
            continue
        held.pop(lid, None)
        mine = (st.setdefault("out", {})).setdefault(lid, {"docs": {}, "mailings": {}, "deadlines": {}, "stage": None})
        refused: list[dict[str, str]] = []
        try:
            _case_out(data_root, client, mid, info, out_dir, mine, calendar, counts)
            failed.pop(lid, None)
        except Stop:
            raise  # every other case would meet the same answer
        except ClioError as exc:  # this case's problem: recorded against it, and the next case goes on
            counts["failed"] += 1
            _error(data_root, f"{info.get('name') or lid}: {exc}", case=lid)
            refused = clio_sent.say_failed(str(exc))
            failed[lid] = refused[0]
        try:
            clio_sent.record(out_dir, mid, mine, refused, by_hand)
        except OSError as exc:  # the case's own record couldn't be written: said on the console; the sync's own record (state.json) is kept
            _log(f"{lid}: clio_sent.json not written ({type(exc).__name__})")
    _update_state(data_root, lambda s: s.update(held=held, failed=failed))
    return counts


def _case_out(data_root: Path | None, client: Clio, mid: str, info: dict[str, Any], out_dir: Path, mine: dict[str, Any], calendar: Any,
              counts: dict[str, int]) -> None:
    """One case out to its matter: documents, mailing records, the stage, the deadlines. Each step is saved as it is done,
    so a refusal part way keeps what was already sent."""
    lid = info["case"]
    name = info.get("name") or lid

    def save():
        _update_state(data_root, lambda s: s.setdefault("out", {}).__setitem__(lid, mine))

    # documents: each packet and its review bundle, a new version when the file changed
    for key, pdf, manifest in _packets(out_dir):
        title = _title(manifest.get("filing"))
        for kind, path, doc_name in ((key, pdf, f"Filing packet - {title}.pdf"),
                                     (key + "_review_bundle", pdf.with_name(pdf.stem + "_review_bundle.pdf"), f"Review bundle - {title}.pdf")):
            if not path.exists():
                continue
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            sent = mine["docs"].get(kind) or {}
            if sent.get("sha256") == digest:
                continue
            label = "review bundle" if kind.endswith("_review_bundle") else "filing packet"
            doc_id = client.upload(mid, data, doc_name, kind, version_of=sent.get("id"), what=f"the upload of the {label} for {name}")
            mine["docs"][kind] = {"id": sent.get("id") or doc_id, "sha256": digest, "at": clock.stamp()}
            counts["documents"] += 1
            save()
    # the mailing records (prefile.record_filing): one document each, once
    status = _read(out_dir / "status.json", {}) or {}
    for rec in status.get("filings") or []:
        key = f"{rec.get('filing')}:{rec.get('at')}"
        if key in mine["mailings"]:
            continue
        doc_name = f"Mailing record - {rec.get('title') or _title(rec.get('filing'))} - {clock.us_date(rec.get('mailed_on')).replace('/', '-') or 'no date'}.pdf"
        mine["mailings"][key] = client.upload(mid, mailing_pdf(name, rec), doc_name, "mailing", what=f"the upload of a mailing record for {name}")
        counts["documents"] += 1
        save()
    # the stage, as a note when it changes; the deadlines, as calendar entries
    try:
        view = case_view(out_dir)
    except Exception as exc:  # noqa: BLE001 -- one case that can't be worked out doesn't stop the others
        _log(f"{lid}: journey failed: {type(exc).__name__}: {exc}")
        _error(data_root, f"{name}: the case's stage and deadlines couldn't be worked out, so none were sent.", case=lid)
        return
    if view["stage"] and view["stage"] != mine.get("stage"):
        client.note(mid, f"Case stage: {view['stage']}", f"{view['stage']}. {view['why']}".strip() + f"\n\nFrom the firm's review app, {clock.us_date(clock.stamp())}.",
                    what=f"the note with the case's stage for {name}")
        mine["stage"] = view["stage"]
        mine["stage_at"] = clock.stamp()
        counts["notes"] += 1
        save()
    _end_out(client, mid, name, view.get("end"), mine, counts, save)
    _tasks_out(data_root, client, mid, name, view.get("tasks") or [], mine, counts, save)
    if not calendar:
        return
    open_ids = set()
    for d in view["deadlines"]:
        did = d["id"]
        done = view["done"].get(did)
        entry = mine["deadlines"].get(did) or {}
        when = date.fromisoformat(d["date"])
        summary = _summary(d["what"])
        description = (f"{d['what']}\nWho: {d.get('who_name') or d.get('owner') or '-'}" + (f"\nNote: {d['note']}" if d.get("note") else "")
                       + (f"\nSource: {d['source']}" if d.get("source") else "") + "\nFrom the firm's review app.")
        if done:
            if entry.get("id") and not entry.get("closed"):
                client.calendar_close(entry["id"], _summary("Done: " + d["what"]),
                                      description + f"\nMarked done by {done.get('by') or 'the office'} on {clock.us_date(done.get('at'))}.",
                                      what=f"the calendar entry for {name}'s deadline")
                entry |= {"closed": "done", "at": clock.stamp()}
                counts["entries_closed"] += 1
            mine["deadlines"][did] = entry
            save()
            continue
        open_ids.add(did)
        if not entry.get("id"):
            entry = {"id": client.calendar_entry(mid, str(calendar), when, summary, description, f"{lid}:{did}",
                                                 what=f"a calendar entry for {name}'s deadline"), "date": d["date"], "closed": None}
            counts["entries_new"] += 1
        elif entry.get("date") != d["date"] or entry.get("closed") or entry.get("summary", summary) != summary:
            client.calendar_entry(mid, str(calendar), when, summary, description, f"{lid}:{did}", entry_id=entry["id"],
                                  what=f"the calendar entry for {name}'s deadline")
            counts["entries_moved"] += 1
            entry |= {"date": d["date"], "closed": None}
        entry["summary"] = summary
        mine["deadlines"][did] = entry
        save()
    for did, entry in mine["deadlines"].items():  # gone from the case's list without a "done" mark: answered, decided, or no longer due
        if did not in open_ids and entry.get("id") and not entry.get("closed"):
            client.calendar_close(entry["id"], _summary("No longer due: " + str(entry.get("summary") or did)),
                                  f"This deadline is no longer on the case in the firm's review app ({clock.us_date(clock.stamp())}).",
                                  what=f"the calendar entry for {name}'s deadline")
            entry |= {"closed": "gone", "at": clock.stamp()}
            counts["entries_closed"] += 1
            save()


def _end_out(client: Clio, mid: str, name: str, end: dict[str, Any] | None, mine: dict[str, Any], counts: dict[str, int], save) -> None:
    """A case's end state (src/engagement.py: declined, withdrawn, transferred, closed) as one matter note, "Closed on 10/03/2026 in the firm's
    review app", the first time it is seen; a reopened case gets a note that says so. The matter's status in Clio is never changed: the firm
    closes and reopens its own matters there (docs/decisions.md)."""
    sent = mine.get("end") or {}
    if end and not sent.get("on") or (end and sent.get("reopened") and end.get("since") != sent.get("on")):
        day = clock.us_date(end.get("since")) or clock.us_date(clock.stamp())
        words = end["name"]
        client.note(mid, f"{words} on {day}", f"{words} on {day} in the firm's review app. The matter's status in Clio is not changed from here: "
                    "the firm changes it in Clio itself.", what=f"the note that the case ended, for {name}")
        mine["end"] = {"state": end["state"], "name": words, "on": end.get("since") or clock.today().isoformat(), "at": clock.stamp()}
        counts["notes"] += 1
        save()
    elif not end and sent.get("on") and not sent.get("reopened"):
        day = clock.us_date(clock.stamp())
        client.note(mid, f"Reopened on {day}", f"The case was reopened on {day} in the firm's review app. The matter's status in Clio is not changed from here.",
                    what=f"the note that the case was reopened, for {name}")
        mine["end"] = sent | {"reopened": clock.stamp()}
        counts["notes"] += 1
        save()


def _task_text(t: dict[str, Any], how: str, me: dict[str, Any] | None) -> str:
    """A Clio task's description: the note, who is responsible (and, when no Clio user has their e-mail, who it was given to instead), where it is from."""
    who = t.get("who_name")
    given = (f"Given to {(me or {}).get('name') or 'the connected Clio user'} here because no Clio user has {who or 'the person'}'s e-mail address."
             if how == "fallback" and who else "")
    return "\n".join(x for x in (str(t.get("note") or "").strip(), f"Responsible: {who}" if who else "", given, "From the firm's review app.") if x)


def _tasks_out(data_root: Path | None, client: Clio, mid: str, name: str, tasks: list[dict[str, Any]], mine: dict[str, Any], counts: dict[str, int], save) -> None:
    """The case's tasks as Clio tasks: made once (given to the Clio user with the person's e-mail, else to the connected user, with the person's name in the
    description), marked complete when done, never deleted. A task done before it was ever sent is not sent."""
    sent = mine.setdefault("tasks", {})
    me = (state(data_root).get("connected") or {}).get("user")
    for t in tasks:
        tid = t["id"]
        rec = sent.get(tid) or {}
        if t.get("done"):
            if rec.get("id") and not rec.get("closed"):
                client.task_complete(rec["id"], what=f"marking a task complete for {name}")
                sent[tid] = rec | {"closed": "done", "at": clock.stamp()}
                counts["tasks_closed"] += 1
                save()
            continue
        email = str(t.get("who") or "").strip().lower()
        if rec.get("id"):  # sent before: a new date or a different person responsible is changed in Clio, as a moved deadline is
            if not rec.get("closed") and (rec.get("date") != t["date"] or rec.get("who", email) != email):
                user, how = client.assignee(email, me)
                client.task_update(rec["id"], user, _task_text(t, how, me), date.fromisoformat(t["date"]), what=f"a task for {name}")
                sent[tid] = rec | {"date": t["date"], "who": email, "assigned": how, "at": clock.stamp()}
                counts["tasks_moved"] += 1
                save()
            continue
        user, how = client.assignee(email, me)
        made = client.task(mid, user, _summary(t["what"], 200), _task_text(t, how, me), date.fromisoformat(t["date"]), what=f"a task for {name}")
        sent[tid] = {"id": made, "date": t["date"], "who": email, "assigned": how, "closed": None, "at": clock.stamp()}
        counts["tasks_new"] += 1
        save()


def run(data_root: Path | None, clients_root: Path, out_root: Path, portal_root: Path | None, direction: str = "both",
        transport: httpx.BaseTransport | None = None, env: dict[str, str] | None = None, pace: Pace | None = None) -> str:
    """One sync ("in", "out" or "both"); the line for the morning report and Keeping current."""
    why = ready(data_root, env)
    if why:
        line = f"Clio: not syncing ({why})."
        if folder(data_root).exists():  # never set up here: nothing is written
            _update_state(data_root, lambda s: s.__setitem__("last_sync", {"at": clock.stamp(), "direction": direction, "line": line, "ok": None}))
        return line
    client = Clio(data_root, transport=transport, env=env, pace=pace)
    waited0 = client.pace.waited  # the queue is the process's: only what this run waited is its own
    parts, ok = [], True
    try:
        if direction in ("in", "both"):
            from . import clio_hooks

            waiting = clio_hooks.snapshot(data_root)  # what Clio's webhooks said changed: this read covers all of it (clio_hooks.py)
            got = sync_in(data_root, clients_root, portal_root, client, out_root)
            clio_hooks.clear(data_root, waiting)
            parts.append(f"{got['matters']} matter(s) read, {len(got['new_clients'])} new client(s), {got['files']} document(s) copied in"
                         + (f", {len(got['protected'])} protected case(s) restricted from the start and not invited: the office gives "
                            "those clients their link in person" if got["protected"] else "")
                         + (f", {len(got['conflicts'])} new client(s) wait for an attorney's conflict decision (Settings, Conflict checks) before any "
                            "invitation" if got.get("conflicts") else "")
                         + (f", {len(waiting['touched'])} of them named by Clio's webhooks" if waiting["touched"] else ""))
        if direction in ("out", "both"):
            c = sync_out(data_root, out_root, client)
            parts.append(f"{c['documents']} document(s) and {c['notes']} note(s) sent, deadlines {c['entries_new']} added, {c['entries_moved']} moved, "
                         f"{c['entries_closed']} closed" + (f", tasks {c['tasks_new']} added, {c['tasks_closed']} completed" + (f", {c['tasks_moved']} changed" if c["tasks_moved"] else "")
                                                         if c["tasks_new"] or c["tasks_closed"] or c["tasks_moved"] else "")
                         + (f", {c['held']} protected case(s) held back" if c["held"] else "")
                         + (f", {c['failed']} case(s) had a problem (Recent problems, in Settings, Connections)" if c["failed"] else ""))
            ok = not c["failed"]
    except ClioError as exc:
        ok = False
        parts.append(str(exc))
        _error(data_root, str(exc))
    except httpx.HTTPError as exc:
        ok = False
        _log(f"unreachable: {type(exc).__name__}")
        parts.append("Clio couldn't be reached: it is tried again at the next sync.")
        _error(data_root, parts[-1])
    line = "Clio: " + "; ".join(parts) + (f" ({client.calls} requests" + (f", {client.pace.waited - waited0:.0f} s waiting for Clio's limit" if client.pace.waited - waited0 >= 1 else "")
                                            + ").")
    _update_state(data_root, lambda s: s.__setitem__("last_sync", {"at": clock.stamp(), "direction": direction, "line": line, "ok": ok}))
    return line


def check(data_root: Path | None = None, transport: httpx.BaseTransport | None = None, env: dict[str, str] | None = None) -> tuple[bool, list[str]]:
    """The read-only test (Settings "Test", `python -m connectors.check clio`): whose connection, how many matters the
    mapping reaches, the first one's documents. Nothing is downloaded, uploaded or written in Clio."""
    try:
        client = Clio(data_root, transport=transport, env=env)
        me = client.call("GET", "/users/who_am_i.json", params={"fields": "id,name,email"}).json().get("data") or {}
        lines = [f"Connected to Clio as {me.get('name') or 'the firm user'}."]
        areas = client.cfg.get("practice_areas") or {}
        if not areas:
            return True, lines + ["No practice area is mapped to a questionnaire yet: no matter would be read."]
        total, first = 0, None
        for pid, area in areas.items():
            found = client.call("GET", "/matters.json", params={"practice_area_id": pid, "status": "open,pending", "fields": "id,display_number",
                                                                "order": "id(asc)", "limit": PAGE}).json()
            rows = found.get("data") or []
            more = bool(((found.get("meta") or {}).get("paging") or {}).get("next"))
            total += len(rows)
            first = first or (rows[0] if rows else None)
            lines.append(f"{area.get('name') or 'A practice area'}: {len(rows)}{' or more' if more else ''} open or pending matter(s).")
        if first:
            docs = client.call("GET", "/documents.json", params={"matter_id": first["id"], "fields": "id", "limit": PAGE}).json().get("data") or []
            lines.append(f"The first matter ({first.get('display_number') or first['id']}) has {len(docs)} document(s).")
        return True, lines
    except ClioError as exc:
        return False, [str(exc)]
    except httpx.HTTPError as exc:
        _log(f"unreachable: {type(exc).__name__}")
        return False, ["Clio couldn't be reached. Check this server's internet connection and try again."]


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def nightly(data_root: Path, clients_root: Path, out_root: Path, direction: str) -> str:
    """The overnight run's step (src/overnight.py): documents in before the clients are processed, results out after."""
    if folder(data_root).exists():  # the Settings page's "Sync now" mirrors into the same client folders
        _update_state(data_root, lambda s: s.__setitem__("clients_root", str(Path(clients_root).resolve())))
    try:
        with step_lock(data_root, wait=20 * 60) if folder(data_root).exists() else _nothing():  # the hourly read or Send now may be running: wait for it
            line = run(data_root, clients_root, out_root, data_root / "portal", direction)
            if direction in ("in", "both") and folder(data_root).exists():  # the webhook subscriptions are renewed before Clio ends them (clio_hooks.py)
                from . import clio_hooks

                renewed = clio_hooks.renew(data_root)
                if renewed:
                    line += " " + renewed
    except Busy:
        return "Clio: another Clio sync was still running after 20 minutes, so this step was skipped tonight."
    return line


# -- what the Settings page and Keeping current show ------------------------------------------------------------------


def allow(data_root: Path | None, case: str, who: str, allowed: bool = True) -> None:
    """The attorney lets a protected case's packet, notes and deadlines go to Clio (or takes that back)."""
    if not str(who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")

    def change(s):
        if allowed:
            s.setdefault("allowed", {})[case] = {"by": who.strip(), "at": clock.stamp()}
        else:
            (s.get("allowed") or {}).pop(case, None)
            s.setdefault("allowed_history", []).append({"case": case, "withdrawn_by": who.strip(), "at": clock.stamp()})
    _update_state(data_root, change)


def view(data_root: Path | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Everything the Connections section shows about Clio; never a secret or a token."""
    env = os.environ if env is None else env
    cfg, st = settings(data_root), state(data_root)
    try:
        saved = Vault(data_root, env).read()
        vault_problem = None
    except NotReady as exc:
        saved, vault_problem = {}, str(exc)
    import offices

    names = {m["case"]: m.get("name") for m in (st.get("matters") or {}).values()}
    from . import clio_hooks

    return {"webhook": clio_hooks.view(data_root, env), "uploads_hourly": bool(cfg.get("uploads_hourly")),
            "failed": [{"case": c, "name": names.get(c) or c, "at": f.get("at"), "what": f.get("what")} for c, f in sorted((st.get("failed") or {}).items())],
            "on": bool(cfg.get("on")), "client_id": env.get("CLIO_CLIENT_ID") or cfg.get("client_id") or "",
            "secret_saved": bool(saved.get("client_secret")) or firmsecrets.is_set("clio.client_secret", env=env, data_root=data_root),
            "from_server": bool(env.get("CLIO_CLIENT_ID")) or firmsecrets.where("clio.client_secret", env=env, data_root=data_root) == "environment",
            "redirect_uri": cfg.get("redirect_uri") or "", "redirect_default": _default_redirect(),
            "connected": st.get("connected") if saved.get("refresh_token") else None, "problem": vault_problem,
            "refresh_failed": st.get("refresh_failed"), "last_sync": st.get("last_sync"), "errors": (st.get("errors") or [])[-5:][::-1],
            "practice_areas": cfg.get("practice_areas") or {}, "attorney_offices": cfg.get("attorney_offices") or {},
            "language_field": cfg.get("language_field"), "calendar": cfg.get("calendar"), "options": st.get("options"),
            "matters": len(st.get("matters") or {}),
            "held": [{"case": c, "name": names.get(c) or c, "why": w} for c, w in sorted((st.get("held") or {}).items())],
            "allowed": [{"case": c, "name": names.get(c) or c, **a} for c, a in sorted((st.get("allowed") or {}).items())],
            "questionnaires": [[k, v] for k, v in QUESTIONNAIRES.items()], "offices": [[o["id"], o["name"]] for o in offices.offices()],
            "updated_by": cfg.get("updated_by"), "updated_at": cfg.get("updated_at"), "ready": ready(data_root, env)}


def _default_redirect() -> str:
    try:
        from . import signin

        return signin.config().get("redirect_uri") or ""
    except Exception:  # noqa: BLE001 -- a connectors file that can't be read means no default
        return ""


def state_text(data_root: Path | None = None, env: dict[str, str] | None = None) -> tuple[str, bool]:
    """(the Keeping current line, whether it needs the firm): connected or not, the last sync, a refusal to renew."""
    cfg, st = settings(data_root), state(data_root)
    if not cfg.get("on"):
        return "Clio: switched off. An attorney switches it on in Settings, Connections.", False
    if st.get("refresh_failed"):
        return (f"Clio: the connection stopped on {clock.us_date(st['refresh_failed']['at'])}: Clio refused to renew it. "
                "An attorney connects it again in Settings, Connections."), True
    why = ready(data_root, env)
    if why:
        return f"Clio: not syncing ({why}). An attorney finishes it in Settings, Connections.", True
    last = st.get("last_sync") or {}
    connected = st.get("connected") or {}
    who = (connected.get("user") or {}).get("name") or "the firm's Clio user"
    if not last or (connected.get("at") and clock.parse(connected["at"]) >= clock.parse(last["at"])):
        return f"Clio: connected as {who}; not synced since it was connected (it runs every night).", False
    fails = len(st.get("failed") or {})
    refused = (f" {fails} case{' has' if fails == 1 else 's have'} something Clio refused (Settings, Connections, Cases Clio refused); "
               "the overnight run tries again.") if fails else ""
    return (f"Clio: connected as {who}. Last sync {clock.us_date(last['at'])}: {str(last.get('line') or '').removeprefix('Clio: ')}" + refused,
            last.get("ok") is False)


def others(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Google Drive, Microsoft 365 and Filevine: whether the server has their keys and whether they are switched on
    (schemas/registers/connectors.json "active"). Their keys are service accounts and tokens that IT installs on the server
    (docs/integrations.md); nothing of theirs is typed on the Settings page."""
    env = os.environ if env is None else env
    from . import signin

    path = signin.CONNECTORS
    data = _read(path, {}) or {}
    active = data.get("active") or {}
    import firmsecrets

    needs = {"google_drive": ((), ("gdrive.service_account",)), "microsoft": (("MS_TENANT_ID", "MS_CLIENT_ID"), ("microsoft.client_secret",)),
             "filevine": (("FILEVINE_CLIENT_ID", "FILEVINE_ORG_ID", "FILEVINE_USER_ID"), ("filevine.pat", "filevine.client_secret"))}  # (ids in the environment, secrets by name)
    folders = {"google_drive": (data.get("google_drive") or {}).get("root_folder_id"),
               "microsoft": (data.get("microsoft") or {}).get("site") or (data.get("microsoft") or {}).get("drive_id"),
               "filevine": (data.get("filevine") or {}).get("project_type_id")}
    out = []
    for cid, label in (("google_drive", "Google Drive"), ("microsoft", "Microsoft 365 (SharePoint or OneDrive)"), ("filevine", "Filevine")):
        keys = all(env.get(k) for k in needs[cid][0]) and all(firmsecrets.configured(n, env=env) for n in needs[cid][1])
        on = cid in (active.get("documents"), active.get("results"))
        state_words = ("Switched on" if on else "Not switched on") + (": the server has its keys" if keys else ": the server doesn't have its keys yet")
        if keys and not folders[cid]:
            state_words += ", and where the client folders are isn't set"
        out.append({"id": cid, "name": label, "on": on, "keys": keys, "where": bool(folders[cid]), "state": state_words + "."})
    return out
