"""Local review app: a paralegal/attorney works through each client's
flags with the scan crop, the sources and an input side by side, and every
decision is recorded (who, when, why) and applied to a re-filled I-485.

It serves real client data (names, SSNs, scans), so by design:
  - it binds to 127.0.0.1 unless staff accounts exist (review/auth.py,
    created with review/users.py) -- then it may serve the office network,
    and every request needs a signed-in person; the role decides who may
    give legal sign-off;
  - it rejects any Host header but its own names (so a web page elsewhere
    can't reach it through DNS rebinding);
  - every write needs the custom X-Review-App header, which a cross-site
    form or simple request can't send;
  - it serves only files that are actually in that client's own folder;
  - responses are marked no-store, and may not be framed by another page;
  - a request body is capped before it is read (413), and a client that
    stalls mid-request is dropped after 30 seconds;
  - behind the firm's TLS proxy (--behind-tls-proxy, or --trusted-proxy
    and its X-Forwarded-Proto: https), every response carries HSTS and the
    session cookie is Secure;
  - every opening of a case, a scan, a filled form, a packet or the
    client's answers is logged (who, role, which client and file, when,
    from where): review_views.jsonl, read back by the attorney's "Who
    viewed this" on the Decision log;
  - staff may sign in with the firm's Microsoft 365 or Google account
    (connectors/signin.py) once that is switched on, at /auth/start and
    /auth/callback.
Python standard library only (plus the connectors' httpx for staff sign-in);
it stays on http.server: reach it through the office network or a VPN, behind
a TLS proxy (docs/hardening.md), never directly from the internet.

    python src/review/server.py            # then open http://127.0.0.1:8485
    python src/review/server.py --port 9000 --data data/clients
    python src/review/server.py --host 0.0.0.0 --hostname review.office.lan   # office network, accounts required
    python src/review/server.py --hostname review.office.lan --trusted-proxy 127.0.0.1   # behind Caddy or nginx on this machine
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import secrets
import sys
import threading
import time
from collections import Counter, OrderedDict
from contextlib import ExitStack, contextmanager
from datetime import date, timedelta
from html import escape
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fill import load_field_map  # noqa: E402
from law_app.bootstrap.config import staff_arguments  # noqa: E402
from law_app.bootstrap.composition import build_staff_app  # noqa: E402
from review import oversight, qr  # noqa: E402
from review.roster import Roster  # noqa: E402
from review.auth import COOKIE, DEVICE_COOKIE, REMEMBER, ROLES, WRONG, Accounts, needs_attorney  # noqa: E402
from review.state import (  # noqa: E402
    Catalog,
    build_items,
    load_decisions,
    refill,
    undo_decision,
)
import clock
import case_assignment
from connectors import drive_settings, drive_intake
import events
import jobs
import restricted
import read_scope
import support

STATIC = Path(__file__).resolve().parent / "static"
# Request bodies (bytes), refused with 413 before they are read. Every route takes a small JSON body (clients upload
# through the portal; the one route that takes a file is the office's own scan, below); signing in is reachable by
# anyone, so it gets the least.
MAX_BODY = 1024 * 1024
# a scan the office adds to a case (one file per request, base64 in JSON): the portal's 15 MB, a third more for the encoding
MAX_BODY_UPLOAD = 15 * 1024 * 1024 * 4 // 3 + 4096
MAX_BODY_EVIDENCE = 4 * 1024 * 1024 * 4 // 3 + 4096
MAX_BODY_OPEN = 16 * 1024
REQUEST_TIMEOUT = 30  # seconds a connection may sit silent mid-request before it is dropped
HSTS = "max-age=31536000; includeSubDomains"  # the portal's (src/portal/app.py)
# What every answer carries (make_handler's _headers: the one place for _send, _stream, _export_download and http.server's own errors). The page
# asks for no camera, microphone or location; nothing of another site may open it in a window it controls, or load its answers (COOP, CORP).
PERMISSIONS = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
NONCE = b'nonce="%%NONCE%%"'  # in a page's <script> and <style> tags (index.html, review/answers_page.py): a fresh value for each answer
VIEW_LOG_FAILED = "The record of who opened this could not be written, so it is not shown. Try again in a moment; if it keeps happening, the server's disk may be full."
SERVER_ERROR = "Something went wrong on the server. Try again in a moment; if it keeps happening, tell {support}."


def csp(content_type: str, nonce: str | None = None) -> str:
    """The Content-Security-Policy for an answer of this type. A page (text/html): its one script and its one style block run only with this answer's
    nonce ('unsafe-inline' is gone for scripts); inline style *attributes* stay allowed (style-src-attr 'unsafe-inline': the page builds its screens with
    style="..." on hundreds of elements, and a nonce cannot cover an attribute), and a browser too old for style-src-elem and style-src-attr falls back to
    style-src, which keeps 'unsafe-inline' for styles only. A PDF: the browser's own viewer shows it, and some viewers stop under default-src 'none', so
    a PDF is only kept out of frames. Anything else (JSON, CSV, images, zips, the calendar file): nothing may run or load."""
    kind = content_type.split(";")[0].strip().lower()
    if kind == "text/html" and nonce:
        return (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; style-src-elem 'self' 'nonce-{nonce}'; "
                "style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
    if kind == "application/pdf":
        return "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    return "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
# Signing in with Microsoft or Google: from the button to the provider sending the person back.
SIGN_IN_WINDOW = 600
MAX_PENDING = 1000  # sign-ins under way at once; beyond, the oldest is dropped (/auth/start needs no sign-in)
# Attempts per address (the trusted proxy's X-Forwarded-For, else the connection's) per minute, on the routes anyone
# can reach: a password check costs 0.3 s and 128 MiB, a "Sign in with ..." keeps a state for 10 minutes. Generous
# enough for an office whose staff share one address.
ATTEMPTS = {"/api/login": 20, "/api/password": 20, "/auth/start": 20, "/api/code": 20, "/api/setup": 20,
            # Clio's webhook: every call from an address (Clio sends a burst when many documents are added) and, apart, the ones refused (guessing a signature)
            "/clio/webhook": 600, "/clio/webhook-bad": 20,
            # the routes anyone reaches without a session (OPEN_GETS), counted only for a request with none: the page, who-am-I, the provider sending someone
            # back; and the calendar feed (TOKENED: guessing a 32-byte token is hopeless, a flood is not; a calendar program asks every few minutes at most)
            "/": 120, "/api/me": 120, "/auth/callback": 20, "/calendar/": 60}
OPEN_GETS = frozenset({"/", "/api/me", "/auth/callback"})
NOT_ALERTED = frozenset({"/clio/webhook"})  # every call Clio makes: a burst of uploads is not someone guessing
ALERT_AFTER = 3  # an address refused for too many tries this many times in a day: a row in the access log (lockout_alert) and a line in the morning report
TOO_MANY_REQUESTS = "Too many requests from this address. Wait a minute."
# The code after the password (review/auth.py verify_code) and setting the app up share one allowance per address; each wrong
# code also counts toward the account's lockout, the same count as a wrong password (5 in a row: 15 minutes).
# /api/setup: the first attorney (a setup code is guessed here too), its own allowance.
ATTEMPTS_AS = {"/api/enrol": "/api/code"}
TOO_MANY = "Too many sign-in attempts from this computer. Wait a minute and try again."
SIGN_IN_COOKIE = "review_signin"  # ties the provider's answer to the browser that asked (state), SameSite=Lax to survive the redirect
SIGN_IN_MESSAGES = {  # /?signin=<code> after the callback: the sign-in screen shows these words, never the provider's
    "refused": WRONG,  # a valid account that isn't on the staff list, or is turned off: the words of a wrong password
    "failed": "The sign-in didn't finish. Try again, or sign in with your email and password.",
    "expired": "That sign-in took too long or was started in another window. Try again.",
    "cancelled": "The sign-in was cancelled.",
}
# What a row of the view log says was opened; a case or a scan crop opened again by the same person within
# REPEAT seconds is one row (a card asks for several crops at once, and the case reloads after each decision).
VIEW_KINDS = {"case": "Opened the case", "scan": "Looked at a scan", "document": "Opened a document", "filled_form": "Opened a filled form",
              "packet": "Opened the filing packet", "translation": "Opened a document's translation", "declaration": "Opened the client's declaration", "online_bundle": "Downloaded the online-filing files", "answers": "Read the portal answers",
              "rfe_response": "Opened a USCIS request response", "review_bundle": "Opened the review bundle", "documents": "Listed the case's documents",
              "link": "Showed the client's portal sign-in link", "export": "Downloaded the export of the firm's data",
              "letter": "Opened a letter to the client", "case_file": "Downloaded the client's file to hand over",
              "case_summary": "Downloaded the summary for the attorney", "prospect": "Opened a first-call record", "prospect_letter": "Opened a letter to a prospect",
              "prepare_sheet": "Printed the client's preparation sheet", "fee_waiver_form": "Opened the fee waiver request form (EOIR-26A)",
              "notice": "Looked at a notice waiting in the inbox"}
REPEATS, REPEAT = ("case", "scan", "documents"), 600
EXPORT_NAME = re.compile(r"i485-firm-data-(\d{4}-\d{2}-\d{2})(?:-\d+)?\.zip")  # the export's own files (tools/export_firm.py everything), in data/exports/
# The routes that open one client (?client= on a GET, "client" in a POST). Each is gated before it runs (src/restricted.py): an
# id that is neither a case folder this person may see nor a client in the portal gets the one answer, 404 "unknown client", the
# same for a case that does not exist and one that is hidden from them. Every other route ignores a "client" it is sent.
# tests/test_restricted.py checks these sets against the routes' own code.
CASE_GET = frozenset({"/api/access_log", "/api/access_log.csv", "/api/requests", "/api/messages", "/api/items", "/api/crop", "/api/answers", "/api/questionnaire.pdf", "/api/file", "/api/documents", "/api/document-text", "/api/capture-image",
                      "/api/packet", "/api/translation.pdf", "/api/declaration.pdf", "/api/packet.pdf", "/api/online-bundle.zip", "/api/review-bundle.pdf", "/api/i360",
                      "/api/rfe", "/api/rfe.pdf", "/api/journey", "/api/path", "/api/prefile", "/api/form", "/api/filled", "/api/case_events", "/api/case_events.csv",
                      "/api/engagement", "/api/engagement.pdf", "/api/case-file.zip", "/api/eoir26a", "/api/eoir26a.pdf",
                      "/api/case-questions", "/api/case-summary.pdf", "/api/prepare-sheet.pdf", "/api/case-notes", "/api/apply-for", "/api/g28", "/api/absence", "/api/jobs",
                      "/api/rebuild", "/api/audit-fill", "/api/explain", "/api/purge", "/api/communication", "/api/assignment", "/api/staff-upload-outcome", "/api/drive-selection", "/api/family-candidates", "/api/family-link-recovery"})
CASE_POST = frozenset({"/api/decide", "/api/undo", "/api/apply", "/api/filed", "/api/packet", "/api/packet-file", "/api/document", "/api/translation", "/api/declaration",
                       "/api/filing-mode", "/api/online-bundle", "/api/review-bundle", "/api/receipt", "/api/i360", "/api/family", "/api/journey", "/api/path",
                       "/api/n400", "/api/i589", "/api/answers", "/api/rfe", "/api/rfe-build", "/api/office", "/api/remind", "/api/ask",
                       "/api/ask-preview", "/api/ask-send", "/api/message-preview", "/api/message-reply", "/api/message-done", "/api/ask-drop",
                       "/api/access", "/api/client-invite", "/api/client-link", "/api/client-questionnaire", "/api/client-upload",
                       "/api/filing-ask", "/api/request-done", "/api/engagement", "/api/engagement-preview", "/api/engagement-paper", "/api/eoir26a", "/api/eoir26a-paper",
                       "/api/filing-ask", "/api/request-done", "/api/conflict-decide", "/api/case-question", "/api/case-summary", "/api/deadline", "/api/case-notes", "/api/apply-for", "/api/g28",
                       "/api/absence", "/api/clio-send", "/api/rebuild", "/api/explain", "/api/purge", "/api/communication", "/api/assignment", "/api/source-setup", "/api/drive-preview", "/api/drive-enqueue", "/api/family-link-recovery"})
# The routes whose address is the credential: /calendar/<token>.ics (src/calendar_feed.py). Unauthenticated by cookie (a calendar program
# has none), authenticated by the token, which opens only its own person's view; an unknown, revoked or made-up token gets UNKNOWN, the one
# answer for a case that does not exist. tests/test_restricted.py classifies them (TOKENED) and tests/test_calendar_feed.py checks them.
TOKENED = frozenset({"/calendar/"})
# The writes that do not take the case's lock (src/jobs.py). A scan the office adds only lands in the case's folder (the worker reads it under the lock): a second one never waits for the
# first. A preview of a question or a message writes nothing, and nor does a question about the case or the summary for the attorney (src/case_questions.py: a model's answer, kept
# in the log). A translation and a declaration spend minutes in a model outside the app's own lock (translation.make, drafting.make_english) and write what a reading never touches:
# holding the case for the whole call would make every other change to the case say it is being read.
# Family recovery takes both case locks itself in sorted order instead of this single-case wrapper.
LOCK_FREE = frozenset({"/api/drive-preview", "/api/drive-enqueue", "/api/client-upload", "/api/ask-preview", "/api/message-preview", "/api/translation", "/api/declaration", "/api/case-question", "/api/case-summary", "/api/family-link-recovery"})
# The writes that change every row of the lists (the firm's settings, its policies, a rule's approval): the lists read every case again before long.
# support (src/support.py): the GET routes that send a file (beside every .pdf, .zip, .csv and .json address), refused in a masked session; and the
# routes never shown to support, plain or masked: the firm's export holds every case, the restricted ones too
SUPPORT_FILES = frozenset({"/api/crop", "/api/file", "/api/answers", "/api/inbox/file", "/api/capture-image", "/api/questionnaire.pdf"})
SUPPORT_NEVER = frozenset({"/api/export-firm", "/api/export-firm.zip", "/api/drive-settings", "/api/drive-settings-audit", "/api/drive-selection", "/api/drive-preview", "/api/drive-enqueue"})
# the firm's own pages a masked session sees with their words (scrubbed of every client value, case ids numbered, never denied): who is signed in, the
# Settings values, the upkeep register, the firm's rules and standard answers
SUPPORT_SHOWN = frozenset({"/api/me", "/api/settings", "/api/maintenance", "/api/rules", "/api/policies"})
FIRM_WIDE = frozenset({"/api/client-wording", "/api/settings", "/api/policies", "/api/rules/approve"})
# Gated helpers may run under the app lock. Enter the installation gate at the
# HTTP boundary before any case/app lock, including their noncase entry points.
GATED_POST = CASE_POST | FIRM_WIDE | frozenset({"/api/client-add", "/api/prospect-new", "/api/prospect-change",
    "/api/contact-recovery", "/api/promotion-recovery", "/api/prospect-communication", "/api/retention",
    "/api/retention-rules", "/api/firm-document", "/api/firm-documents"})
# The route Clio's webhooks call (src/connectors/clio_hooks.py): POST /clio/webhook. Unauthenticated by cookie (Clio has none) and by the X-Review-App header
# every other POST needs; authenticated by the signature on its body (an HMAC-SHA256 with the secret Clio sent when the subscription was made). It is answered
# before anything else in do_POST, and a call that is unsigned, badly signed, too large, for a subscription Clio has not reported enabled, or sent while nothing is
# subscribed gets the answer a made-up POST path gets (403 "forbidden"); a replay of a signed message (Clio's own retry) is answered 200 and does nothing. Above 20
# refused calls a minute from one address the answer is 429 (a made-up path is never limited). tests/test_restricted.py classifies it (WEBHOOKS) and
# tests/test_clio_webhook.py checks it.
WEBHOOKS = frozenset({"/clio/webhook"})
UNKNOWN = {"error": "unknown client"}


def _plain_case(case: str) -> bool:
    return bool(case) and "/" not in case and "\\" not in case and case not in (".", "..")


# Back from Clio (Settings, Connections): /?clio=<outcome>#settings:connections; the page shows these words
CLIO_MESSAGES = {"connected": "Connected to Clio.", "declined": "Clio wasn't connected: the permission was declined in Clio.",
                 "expired": "That took too long or was started in another window. Connect again.",
                 "failed": "Clio didn't confirm the connection. Check the client id and secret and the review app's address, then connect again.",
                 "notready": "Clio isn't set up yet: save the Clio app's client id, client secret and the review app's address first."}
# Said on Connections, above everything else about Clio, until the firm's own first connection has worked (docs/integrations.md: no Clio
# account was used to build the connector; its tests run against a simulated Clio built from Clio's published API documentation).
CLIO_UNPROVEN = ("This connection was built from Clio's published documentation and has not yet run against a real Clio account. The names of "
                 "the permissions to give the Clio app will be confirmed on the first connection; Test, once connected, is the way to find out.")


def httpx_errors() -> type:
    import httpx

    return httpx.HTTPError


def _plain_name(name: str) -> bool:
    """A case folder's name and nothing more (no path in it)."""
    return bool(name) and Path(name).name == name and name not in (".", "..")


class ReviewApp:
    def __init__(self, data_root: Path, field_map_path: Path, template_path: Path, policy_path: Path | None,
                 portal_root: Path | None = None, accounts: Accounts | None = None, secure_cookies: bool = False,
                 behind_tls: bool = False, trusted_proxies: tuple[str, ...] = (), sign_in_factory=None, views_log: Path | None = None,
                 local_setup: bool = True, deployment_path: Path | None = None):
        self.data_root = data_root.resolve()
        # None: one person on this machine, who types their name (no sign-in)
        self.accounts = accounts
        self.secure_cookies = secure_cookies  # behind HTTPS: the session cookie is never sent in the clear
        self.behind_tls = behind_tls  # every request reaches us through the firm's TLS proxy
        self.trusted_proxies = set(trusted_proxies)  # their X-Forwarded-Proto / -For are believed; nobody else's
        self.local_setup = local_setup  # "Set up the first attorney" is shown to the computer itself, with a box for the code (off: only to an address carrying it)
        # provider -> a verifier (connectors/signin.provider_for; a fake in the tests)
        self.sign_in_factory = sign_in_factory
        self._providers: dict[str, object] = {}
        self._sign_ins: dict[str, tuple] = {}  # state -> (provider, nonce, expires): sign-ins under way, oldest first
        self._attempts: dict[str, list[float]] = {}  # "route address" -> times of the last minute's attempts
        self._refusals: dict[str, list[float]] = {}  # address -> when each episode of refusals began, the last day's (limited)
        self.clio_transport = None  # connectors/clio.py's HTTP layer: the tests' simulated Clio, else the network
        self._clio_running = threading.Lock()  # one Clio sync at a time from the Settings page
        self.clio_hourly_delay = 300.0  # seconds after Clio's webhook says a matter changed before it is read (a batch of uploads is one read)
        self._clio_confirming = threading.Lock()
        # who opened what: next to the staff accounts' access log, else next to the client folders
        self.views_log = views_log or (accounts.path.with_name("review_views.jsonl") if accounts else self.data_root.parent / "review_views.jsonl")
        self._seen: dict[tuple, float] = {}
        self._log_lock = threading.Lock()
        try:  # a key found in deployment.json moves into the vault and out of the file (the ledger says so); starting up never fails over it
            import firmsecrets

            for line in firmsecrets.migrate_deployment(data_root=self.data_root.parent, deployment_path=deployment_path):
                print(line, file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"The deployment record's key could not be moved into the vault ({type(exc).__name__}); it is tried again at the next start.", file=sys.stderr)
        # what the attorney reads of the two logs on a screen (review/oversight.py): an index over the view log, so a case's rows and the
        # firm's are never found by reading the whole file
        self.views = oversight.Views(self.views_log, VIEW_KINDS, self._staff_names, self._closed_cases)
        self.staff_log = oversight.StaffLog(accounts, self.views, self._rule_labels) if accounts else None
        # the event ledger (src/events.py): "What changed on this case" and, for the attorney, across the firm, read through an index of its own
        self.events = oversight.Events(events.base_path(self.data_root.parent), {k: v["name"] for k, v in events.KINDS.items()})
        self._export: dict = {}  # the export of the firm's data, running or last finished in this process (export_start)
        self._export_lock = threading.Lock()
        self.portal_root = portal_root  # data/portal: clients invited through the portal, before their case is processed
        if portal_root is not None:  # a client's typed answer to the office's question is read from here as the case is read (review/state.py)
            from review import state as review_state

            review_state.PORTALS[self.data_root] = Path(portal_root).resolve()
            try:  # an add that stopped part way more than an hour ago (src/conflicts.py sweep): out of the people index and the lists
                import conflicts

                conflicts.sweep(self.data_root, Path(portal_root))
            except Exception as exc:  # noqa: BLE001 -- the search checks the cases it finds the same way
                sys.stderr.write(f"conflict checks not swept: {type(exc).__name__}\n")
        self.field_map = load_field_map(field_map_path)
        self.template = template_path
        policies = json.loads(policy_path.read_text(encoding="utf-8"))["policies"] if policy_path and policy_path.exists() else []
        self.catalog = Catalog(self.field_map, template_path, policies)
        self._pages: OrderedDict[tuple, object] = OrderedDict()
        self._lock = threading.Lock()  # decisions/refill write files; one at a time
        self._lock_feedback = threading.Lock()
        self._feedback_at, self._feedback_thread = -1e9, None  # the clients' feedback is brought onto their cases in the background, at most once in I485_FEEDBACK_EVERY seconds
        self._rendering: dict[tuple, threading.Lock] = {}
        # every case's row, kept up to date from the event ledger so a list never walks the case folders (review/roster.py; only when I485_WALK_EVERY is above 0)
        self.roster = Roster(self.data_root, self.field_map, self.template, self.catalog, self.portal_root, hold_unrecorded=self._hold_unrecorded)
        self.jobs_root = jobs.folder_for(self.data_root)  # the reading that takes seconds is a job for the worker (src/jobs.py)

    @property
    def scaled(self) -> bool:
        """The lists read the roster (as installed); with I485_WALK_EVERY=0 (the tests) every ask walks every case, as before."""
        return self.roster.production

    @contextmanager
    def case_write(self, client_id: str, route: str = ""):
        """A write to one case, taken in turn with the job worker and the overnight run (src/jobs.py): waits a few seconds for a reading to finish, then says the case is being read."""
        if route in LOCK_FREE:
            yield
            return
        with jobs.case_lock(self.jobs_root, str(client_id or ""), jobs.BUSY_WAIT):
            yield

    # -- lookups that refuse anything outside known client data --------------

    def client_dir(self, client_id: str) -> Path:
        # one case is always answered from its own folder, never from the lists' copy (a restriction written by another process, a record that cannot be read: seen at once)
        d = self._case_folder(client_id)
        if d is None:
            raise LookupError("unknown client")
        return d

    # -- restricted cases (src/restricted.py): who may see which case ---------------

    def _who(self, role: str | None = None, user: dict | None = None) -> dict | None:
        """The person a list is for: the signed-in user; a role alone (a caller that knows only the role); None when the app
        runs without staff accounts and nobody is named (one person on one machine: nothing is hidden)."""
        if user is not None:
            return user
        return {"role": role} if role else None

    def _case_folder(self, client_id: str, *, inventory=None) -> Path | None:
        """The case folder a client id names, looked up among the case folders as client_dir does (a folder name verbatim:
        spaces, accents and commas included; never a path elsewhere), else None."""
        if not client_id or client_id in (".", "..") or "/" in client_id or "\\" in client_id or "\0" in client_id or len(client_id) > 200:
            return None
        d = self.data_root / client_id
        if not (d / "fact_graph.json").exists() or client_id not in (os.listdir(self.data_root) if inventory is None else inventory):
            return None  # the exact name only: a case-insensitive disk would find "CASE-ROSA" for "case-rosa"
        return d

    def _held_folder(self, client_id: str, *, inventory=None) -> Path | None:
        """A client not processed yet whose case folder holds only its first records: the restriction record of a protected kind
        (restricted.protect_new) and the conflict check made when the client was added, imported or synced (src/conflicts.py); looked
        up by the exact name as _case_folder does, else None."""
        if not client_id or client_id in (".", "..") or "/" in client_id or "\\" in client_id or "\0" in client_id or len(client_id) > 200:
            return None
        d = self.data_root / client_id
        if not ((d / restricted.FILE).exists() or (d / "conflict_check.json").exists()) or client_id not in (os.listdir(self.data_root) if inventory is None else inventory):
            return None
        return d

    def _in_portal(self, client_id: str, *, inventory=None) -> bool:
        """A client in the portal (invited, perhaps not processed yet), by its exact folder name there."""
        if self.portal_root is None or not client_id or client_id in (".", "..") or "/" in client_id or "\\" in client_id or "\0" in client_id or len(client_id) > 200:
            return False
        folder = self.portal_root / "clients"
        return (folder / client_id / "profile.json").exists() and client_id in (os.listdir(folder) if inventory is None else inventory)

    def _hold_unrecorded(self, client_id: str) -> Path | None | bool:
        """A portal client whose profile names a protected kind (the track the office chose, or the Docketwise matter type) but whose case folder
        holds no restriction record and no case file (an import made before records were written, or a record that was taken away): the record
        is written now (restricted.protect_new) and the folder is returned as held. None for a client of no protected kind. Never raises: when the
        record cannot be written the answer is False: the client stays closed to everyone (fail closed)."""
        try:
            profile = json.loads((self.portal_root / "clients" / client_id / "profile.json").read_text(encoding="utf-8"))
            kind = profile.get("track") or profile.get("docketwise_matter_type")
            found = restricted.kind_law(kind)
        except (OSError, ValueError, AttributeError):
            return None
        if not found:
            return None
        d = self.data_root / client_id
        try:
            restricted.protect_new(d, found, str(kind), "Found without a restriction record, kind", "the review app")
        except OSError:
            pass
        if self.scaled:
            self.roster.touch(client_id)  # the lists close it at their next look
        return d if (d / restricted.FILE).exists() else False

    def may_open(self, user: dict | None, client_id: str, *, inventory=None) -> bool:
        """The gate every route that opens one client passes first (CASE_GET, CASE_POST): a case folder this person may see
        (restricted.visible_to), or a client in the portal with no case folder yet. Anything else, a hidden case or an id that
        names nothing, is refused alike, so the answer never tells a restricted case from a made-up name. Without staff accounts
        nobody is hidden, and each route answers for itself as before."""
        if self.accounts is None:
            return True
        names, portal_names = inventory if inventory is not None else (None, None)
        d = self._case_folder(client_id, inventory=names)
        if d is not None:
            return restricted.visible_to(user, d)
        if not self._in_portal(client_id, inventory=portal_names):
            return False
        if not (self.data_root / client_id / restricted.FILE).exists():
            # no restriction record and no case file: a protected kind in the profile is closed now (as restricted.law fails closed), whatever else
            # the folder holds (the conflict check alone, written by an add path that stopped before its record): never opened first
            held = self._hold_unrecorded(client_id)
            if held is False:
                return False
            if held is not None:
                return restricted.visible_to(user, held)
        held = self._held_folder(client_id, inventory=names)  # a VAWA, T, U or asylum client in the portal, closed before any document is read
        # no restriction record and no processed case: a portal-only client, or one the Docketwise importer or the Clio sync made (their
        # case folder holds the documents and nothing else yet). Every client the list shows can be invited; a restricted one is held above.
        return restricted.visible_to(user, held) if held is not None else True

    def visible(self, user: dict | None, client_id: str) -> bool:
        """For a list or a picker: a case this person may see. An id that is no case folder is not a hidden case."""
        d = self._case_folder(client_id) or self._held_folder(client_id)
        if d is not None and self.accounts is not None and not (d / "fact_graph.json").exists() and not (d / restricted.FILE).exists() and self._in_portal(client_id):
            if self._hold_unrecorded(client_id) is False:  # a protected kind with no record yet (the conflict check alone): recorded now, or kept out
                return False
        return d is None or restricted.visible_to(user if self.accounts is not None else None, d)

    def _listed_to(self, user: dict | None, case: str) -> bool:
        """may_open from the lists' own copy when the app keeps one (no file read): only for what asks it thousands of times in one answer (a calendar of every case, a conflict search's
        hits), as a list does. A single case is never answered from here: may_open and visible read the folder. A case the copy does not hold is asked of the folder."""
        if self.scaled and self.accounts is not None:
            self.roster.sync()
            with self.roster.lock:
                entry = self.roster.entries.get(case)
            if entry is not None:
                return not self.roster.hides(user, entry)
        return self.may_open(user, case)

    def _scope(self, who: dict | None) -> dict:
        if self.scaled:
            return self.roster.scope(who)
        return restricted.scope(who, self.data_root)

    def _visible_rows(self, rows: list[dict], who: dict | None) -> list[dict]:
        """The all-clients rows this person may see (restricted.scope's hidden cases left out), each marked restricted or not (a
        case invited through the portal and not processed yet has no case file to say: it is listed, unrestricted, unless it was
        added as a protected kind and holds the restriction record already)."""
        unwritten = set()  # a protected kind whose record could not be written: closed to everyone (fail closed)
        if self.portal_root is not None:
            for row in rows:  # a protected kind whose folder holds the conflict check alone (an add that stopped before its record): recorded first
                d = self.data_root / row["id"]
                if (d / "conflict_check.json").exists() and not (d / restricted.FILE).exists() and not (d / "fact_graph.json").exists():
                    if self._hold_unrecorded(row["id"]) is False:
                        unwritten.add(row["id"])
        hidden = self._scope(who)["hidden"] | (unwritten if self.accounts is not None else set())
        out = []
        for row in rows:
            if row["id"] in hidden:
                continue
            d = self.data_root / row["id"]  # the overview's own ids: the case folders' names
            if (d / "fact_graph.json").exists() or (d / restricted.FILE).exists():
                closed = restricted.is_restricted(d)
                row = row | {"restricted": closed}
                if closed and not (d / "fact_graph.json").exists():
                    row["held_note"] = restricted.INVITE_HELD  # no invitation goes to this client: the office hands the link over
                if closed and (who is None or who.get("role") == "attorney"):  # the attorney's filter "Messages on (restricted)": no one else is told
                    row["messages_on"] = bool((restricted.record(d)["messages"] or {}).get("on"))
            if (d / "conflict_check.json").exists():
                import conflicts

                if conflicts.held(d):  # the conflict check waits for an attorney's decision: no invitation until then
                    row = row | {"conflict_held": conflicts.hold_words(d)}
            out.append(row)
        return out

    def source_snapshot(self, client_id: str, doc: str, expected_sha256: str | None = None):
        from review.evidence import snapshot
        return snapshot(self.client_dir(client_id), doc, expected_sha256=expected_sha256)

    def source_file(self, client_id: str, doc: str) -> Path:
        # Legacy bundle callers still use paths; every lookup now obeys the
        # registered-name, bounded-root and retained-identity checks.
        return self.source_snapshot(client_id, doc).path

    def page_image(self, client_id: str, doc: str, page: int, expected_sha256: str | None = None, *, timeout=10):
        """Recheck bytes and page authority before reusing one original page."""
        from review.evidence import Unavailable, render_source_page_bounded
        deadline = time.monotonic() + timeout
        source = self.source_snapshot(client_id, doc, expected_sha256)
        if source.location(page)["page"] is None:
            raise Unavailable("Original page location unavailable. Open the document instead; read it again before confirming a fact.")
        # Aliases for segments of the same original share pixels only after
        # this request independently validates its own original-page range.
        key = (client_id, source.path.name, source.sha256, page)
        with self._lock:
            if key in self._pages:
                self._pages.move_to_end(key)
                return self._pages[key]
            doc_lock = self._rendering.setdefault(key, threading.Lock())
        if not doc_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise TimeoutError("The optional page preview is busy. Open the original document.")
        try:
            with self._lock:
                image = self._pages.get(key)
            if image is None:
                try:
                    if source.content_type == "application/pdf":
                        image = render_source_page_bounded(source.data, page, deadline - time.monotonic())
                    else:
                        image = render_source_page_bounded(source.data, page, deadline - time.monotonic(), image_only=True)
                    with self._lock:
                        self._pages[key] = image
                        while len(self._pages) > 24:
                            self._pages.popitem(last=False)
                except BaseException:
                    with self._lock:
                        if self._rendering.get(key) is doc_lock:
                            self._rendering.pop(key, None)
                    raise
                with self._lock:
                    if self._rendering.get(key) is doc_lock:
                        self._rendering.pop(key, None)
        finally:
            doc_lock.release()
        return image

    def source_quote_region(self, client_id, source, location, quote, *, deadline=None):
        """Local-only, hash-bound OCR display lookup on the original page."""
        import tempfile
        from collections import OrderedDict
        from classify.ocr import ocr_words
        from review.evidence import quote_line_region
        page = location["page"]
        deadline = deadline if deadline is not None else time.monotonic() + 10
        remaining = lambda: max(0, deadline - time.monotonic())
        if page is None or not location.get("instance_id"):
            return None
        # page_image revalidates source bytes and the alias's allowed page range
        # even when the OCR result below has already been cached.
        try:
            if not remaining():
                return None
            image = self.page_image(client_id, source.alias, page, source.sha256, timeout=remaining())
        except (RuntimeError, OSError, ValueError, __import__("subprocess").SubprocessError):
            return None  # Optional rendering failure leaves the known-page fallback.
        key = (client_id, source.path.name, source.sha256, page)
        with self._lock:
            cache = self.__dict__.setdefault("_preview_words", OrderedDict())
            locating = self.__dict__.setdefault("_preview_locating", {})
            lock = locating.setdefault(key, threading.Lock())
        if not lock.acquire(timeout=remaining()):
            return None  # Another reader is busy; keep the honest page fallback.
        try:
            with self._lock:
                present = key in cache
                words = cache.get(key)
                if present:
                    cache.move_to_end(key)
            if not present:
                # OCR does not hold the shared app lock.
                # Only this page/hash's locator is serialized.
                try:
                    # App-owned source-adjacent scratch supports Windows Tesseract
                    # invoked from WSL; /tmp cannot be read by that executable.
                    scratch_root = self.data_root.parent / "preview-scratch"
                    from portal.queue_bridge import _safe
                    scratch_root = _safe(scratch_root)
                    scratch_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with tempfile.TemporaryDirectory(prefix="i485-preview-ocr-", dir=scratch_root) as scratch:
                        words = ocr_words(image, work_dir=scratch, timeout=max(.01, remaining()))
                except (RuntimeError, OSError, ValueError, UnicodeError, __import__("subprocess").SubprocessError):
                    words = []
                with self._lock:
                    cache[key] = words
                    while len(cache) > 24:
                        cache.popitem(last=False)
            with self._lock:
                if locating.get(key) is lock:
                    locating.pop(key, None)
        finally:
            lock.release()
        # Optional work may have taken seconds. Revalidate even a cached result
        # before its coordinates can be attached to the displayed source.
        self.source_snapshot(client_id, source.alias, source.sha256)
        return quote_line_region(words, quote, image.width, image.height)

    # -- who opened what --------------------------------------------------------

    def viewed(self, user: dict | None, client_id: str, kind: str, name: str = "", address: str = "") -> None:
        """One row per opening of a client's case, scan, filled form, packet or portal answers: who, their
        role, which client and file, when, and from which address. Never a value from the case: the client
        id and the file name only."""
        now = time.time()
        who = (user or {}).get("email")
        if client_id.startswith("prospect:"):  # a first-call record (src/prospects.py): "prospect:<id>"; its folder is closed like a case's
            import prospects

            d = prospects.dir_of(self.data_root, client_id[len("prospect:"):])
        else:
            d = self._case_folder(client_id)
        closed = d is not None and restricted.is_restricted(d)  # the row says so (src/restricted.py)
        with self._log_lock:
            if kind in REPEATS:
                key = (who, client_id, kind, name)
                if now - self._seen.get(key, 0) < REPEAT:
                    return
                if len(self._seen) > 5000:
                    self._seen = {k: t for k, t in self._seen.items() if now - t < REPEAT}
                self._seen[key] = now
            row = {"at": clock.stamp(), "email": who, "name": (user or {}).get("name"), "role": (user or {}).get("role"),
                   "client": client_id, "kind": kind, "file": name or None, "address": address} | ({"restricted": True} if closed else {})
            self.views_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.views_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def access_log(self, client_id: str, role: str | None, limit: int = 50, page: int | None = None, person: str = "", kind: str = "") -> dict:
        """The attorney's "Who viewed this": the client's latest rows, newest first (limit), or every row a page at a time (page: 50 of the rows
        that match the person and the kind). Read through the index (review/oversight.py), never the whole file."""
        if role == "paralegal":
            raise PermissionError("Who viewed a case is the attorney's to see.")
        if page is None:
            return self.views.case(client_id, limit=limit)
        return self.views.case(client_id, page=page, person=person, kind=kind)

    def access_log_csv(self, client_id: str, role: str | None, person: str = "", kind: str = "") -> bytes:
        if role == "paralegal":
            raise PermissionError("Who viewed a case is the attorney's to see.")
        return self.views.case_csv(client_id, person=person, kind=kind)

    # -- what the office did, on a screen (review/oversight.py; the attorney's) ---------------------------

    def _closed_cases(self, names) -> set[str]:
        """Which of these case ids are restricted now (src/restricted.py): for the view log's rows, which carry only the mark of the day they were written."""
        if self.scaled:  # the roster holds each case's restriction: not a look at each folder
            self.roster.names()
            with self.roster.lock:
                return {n for n in names if self.roster.entries.get(n, {}).get("closed") and self.roster.entries[n]["has_case"]}
        out = set()
        for name in names:
            d = self._case_folder(name)
            if d is not None and restricted.is_restricted(d):
                out.add(name)
        return out

    def _staff_names(self) -> dict[str, str]:
        return {u["email"]: u["name"] for u in self.accounts.users()} if self.accounts is not None else {}

    def _rule_labels(self) -> dict[str, str]:
        """How an approval names what was approved, for "What staff did": a rule by its plain name, a policy by its name."""
        from review.state import RULE_NAMES, concise
        from rules import approval

        return {r["id"]: (f"the firm policy “{r.get('name') or r['code']}”" if r["kind"] == "policy"
                          else RULE_NAMES.get(r["id"]) or f"a rule: {concise(r['plain_text'], 90)}") for r in approval.catalog()}

    def _attorneys_only(self, user: dict | None, what: str) -> None:
        """Without staff accounts nobody is a paralegal (one person on one machine); with them, only an attorney reads these."""
        if self.accounts is not None and (user or {}).get("role") != "attorney":
            raise PermissionError(f"{what} is the attorney's to see.")

    @staticmethod
    def _page_of(q: dict) -> int:
        try:
            return int(q.get("page") or 1)
        except ValueError:
            raise ValueError("That is not a page number.") from None

    def staff_did(self, q: dict, user: dict | None, csv: bool = False):
        """Settings, Staff, "What staff did": the access log, rule approvals, policy edits and "link shown" rows, 50 a page, filtered by person,
        kind and the firm's dates (from, to); or all of them as a CSV file."""
        self._attorneys_only(user, "What staff did")
        if self.staff_log is None:
            raise LookupError("This app runs without staff accounts: nobody signs in.")
        args = {"person": str(q.get("person") or ""), "kind": str(q.get("kind") or ""), "start": str(q.get("from") or ""), "end": str(q.get("to") or "")}
        return self.staff_log.csv(**args) if csv else self.staff_log.page(page=self._page_of(q), **args)

    def who_viewed(self, q: dict, user: dict | None, csv: bool = False):
        """Settings, Staff, "Who viewed what": every opening across the firm (or, group=day, the totals by person and day), filtered by person,
        kind, dates and restricted cases only; 50 a page, or all of it as a CSV file."""
        self._attorneys_only(user, "Who viewed what")
        args = {"person": str(q.get("person") or ""), "kind": str(q.get("kind") or ""), "start": str(q.get("from") or ""), "end": str(q.get("to") or ""),
                "restricted": q.get("restricted") == "1", "group": "day" if q.get("group") == "day" else ""}
        return self.views.firm_csv(**args) if csv else self.views.firm(page=self._page_of(q), **args)

    def case_events(self, client_id: str, user: dict | None, q: dict, csv: bool = False):
        """The case page's "What changed on this case": the latest rows of the event ledger (limit), or every row a page at a time, filtered by person and kind;
        or all of them as a CSV file. The route is gated by may_open like every route that opens one case: an attorney, or staff who may open it (the staff an
        attorney named on a restricted case); anyone else is told the case does not exist."""
        person, kind = str(q.get("person") or ""), str(q.get("kind") or "")
        skip = () if self.accounts is None or (user or {}).get("role") == "attorney" else ("purge",)  # a purge's rows are an attorney's (src/purge.py)
        if csv:
            return self.events.csv(case=client_id, person=person, kind=kind, not_kinds=skip)
        if "page" in q:
            return self.events.case(client_id, page=self._page_of(q), person=person, kind=kind, not_kinds=skip)
        return self.events.case(client_id, limit=int(q.get("limit") or 20), not_kinds=skip)

    def firm_events(self, q: dict, user: dict | None, csv: bool = False):
        """Settings, Staff, "What changed across the firm": every row of the ledger, 50 a page, filtered by person, kind, case and the firm's dates (from, to);
        or all of them as a CSV file. The attorney's: and a restricted case's rows are left out for anyone who may not open that case (restricted.scope)."""
        self._attorneys_only(user, "What changed across the firm")
        hidden = self._scope(self._who(None, user))["hidden"] if self.accounts is not None else set()
        args = {"person": str(q.get("person") or ""), "kind": str(q.get("kind") or ""), "start": str(q.get("from") or ""), "end": str(q.get("to") or ""),
                "case": str(q.get("case") or ""), "hidden": hidden}
        return self.events.csv(**args) if csv else self.events.firm(page=self._page_of(q), **args)

    # -- the export of the firm's data (tools/export_firm.py --everything) ---------------------------------------------

    def _exports_folder(self) -> Path:
        return self.data_root.parent / "exports"

    def export_state(self, user: dict | None) -> dict:
        """Settings, Keeping current, "Export the firm's data": whether an export is running, how far it is, the last one's result, and the earlier ones."""
        self._attorneys_only(user, "The export of the firm's data")
        folder = self._exports_folder()
        earlier = []
        for f in sorted(folder.glob("i485-firm-data-*.zip"), reverse=True) if folder.is_dir() else []:
            if EXPORT_NAME.fullmatch(f.name) and f.stat().st_size:  # an empty one is a name an export in progress has claimed
                earlier.append({"name": f.name, "bytes": f.stat().st_size, "day": EXPORT_NAME.fullmatch(f.name).group(1)})
        with self._export_lock:
            return {"export": dict(self._export), "earlier": earlier}

    def export_start(self, user: dict | None) -> dict:
        """Starts the export of all of the firm's data on the server (one at a time; it can take a long while, so the screen asks how it is going).
        The same code the command line runs: tools/export_firm.py everything()."""
        self._attorneys_only(user, "The export of the firm's data")
        who = (user or {}).get("name") or "someone at the firm"
        with self._export_lock:
            running = self._export.get("state") == "running"
            if not running:
                self._export = {"state": "running", "started": clock.stamp(), "by": who, "done": 0, "total": 0}
        if running:  # one at a time: the screen is told how the one under way is going
            return self.export_state(user)
        threading.Thread(target=self._export_run, args=(user, who), daemon=True, name="export-firm-data").start()
        return self.export_state(user)

    def _export_run(self, user: dict | None, who: str) -> None:
        sys.path.append(str(Path(__file__).resolve().parents[2] / "tools"))  # at the end: a tool must never shadow a src module (two files were both named audit_fill)
        try:
            import export_firm

            def progress(done: int, total: int) -> None:
                with self._export_lock:
                    self._export.update(done=done, total=total)

            where = export_firm.default_where(self.data_root, self.portal_root or self.data_root.parent / "portal",
                                              self.accounts.path if self.accounts is not None else self.data_root.parent / "review_users.json")._replace(
                views=self.views_log, exports=self._exports_folder())
            access = (lambda files: self.accounts.log("data_exported", user["email"], files=files)) if self.accounts is not None and user else None  # noqa: E731
            done = export_firm.everything(where, who=who, role=(user or {}).get("role"), via="staff", access_log=access, progress=progress)
            with self._export_lock:
                self._export = {"state": "done", "finished": clock.stamp(), "by": who, "files": done["files"], "bytes": done["bytes"], "name": done["name"],
                                "sha256": done["sha256"], "warnings": done["warnings"]}
        except export_firm.ExportError as exc:
            with self._export_lock:
                self._export = {"state": "failed", "finished": clock.stamp(), "by": who, "why": str(exc)}
        except Exception as exc:  # noqa: BLE001 -- said on the console; the screen gets plain words
            sys.stderr.write(f"export of the firm's data failed ({type(exc).__name__}: {exc})\n")
            with self._export_lock:
                self._export = {"state": "failed", "finished": clock.stamp(), "by": who,
                                "why": "The export could not be finished. Check that the server has room for it, then try again."}

    def export_file(self, name: str, user: dict | None) -> Path:
        """The finished export to download (the attorney's); only a file this feature made, in its own folder."""
        self._attorneys_only(user, "The export of the firm's data")
        if not EXPORT_NAME.fullmatch(name or ""):
            raise LookupError("No such export.")
        path = self._exports_folder() / name
        if not path.is_file() or not path.stat().st_size:
            raise LookupError("No such export.")
        return path

    def _requery(self, client_dir: Path) -> None:
        """The case's rows in the query layer (src/query.py) after a change to its records. A miss is never the person's problem: the nightly run and every
        screen that reads the layer bring it up to date."""
        self.roster.touch(Path(client_dir).name)  # the lists read this case again before the next one is built
        try:
            import query

            query.rebuild(client_dir, query.default_path(self.data_root))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"query layer not updated for a case ({type(exc).__name__})\n")

    def messages_on_cases(self, user: dict | None) -> dict:
        """Restricted cases that send automatic messages: who switched them on, when and why (src/restricted.py), for the attorney to switch off."""
        self._attorneys_only(user, "The list of cases that send automatic messages")
        rows = []
        for row in self._rows(self._who(None, user)):
            if row.get("messages_on"):
                m = restricted.record(self.data_root / row["id"])["messages"] or {}
                rows.append({"id": row["id"], "name": (row.get("summary") or {}).get("name") or "", "by": m.get("by"), "at": m.get("at"), "reason": m.get("reason") or ""})
        return {"cases": sorted(rows, key=lambda r: clock.key(r["at"]), reverse=True)}

    def policy_changes(self, user: dict | None, csv: bool = False):
        """Settings, Firm policies, "Every change": every edit to every policy, newest first (src/rules/firm_policies.py), or a CSV file with one
        line per thing changed."""
        from rules import firm_policies

        self._attorneys_only(user, "The firm policies' record of changes")
        changes = firm_policies.changes(self._policy_labels()["label_for"])
        if not csv:
            return {"changes": changes}
        rows = []
        for c in changes:
            for p in c["parts"] or [{"what": "", "before": "", "after": "", "labels": []}]:
                rows.append({"when": oversight.us_when(c["at"]), "who": c["by"], "policy": c["name"], "what": c["what"],
                             "part": p["what"] + (": " + "; ".join(p["labels"]) if p["labels"] else ""), "before": p["before"], "after": p["after"]})
        return oversight.csv_file([("when", "When"), ("who", "Who"), ("policy", "Policy"), ("what", "What happened"), ("part", "What changed"), ("before", "Before"), ("after", "After")], rows)

    # -- signing in with the firm's Microsoft 365 or Google account (connectors/signin.py) --

    def sign_in_providers(self) -> list[dict]:
        """The "Sign in with ..." buttons: only providers switched on and set up, and only with staff accounts."""
        if self.accounts is None:
            return []
        from connectors import signin

        return signin.providers()

    def _provider(self, name: str):
        """The provider's verifier; its redirect URI is connectors.json's, never the request's Host header."""
        if name not in self._providers:
            from connectors import signin

            self._providers[name] = (self.sign_in_factory or signin.provider_for)(name)
        return self._providers[name]

    def limited(self, route: str, address: str) -> bool:
        """One more attempt at a route anyone can reach; True when this address went over ATTEMPTS[route] in a minute. A refusal starts an episode
        (another starts a minute later if the refusals go on); the ALERT_AFTER-th episode of one address in a day is an alert (_refused)."""
        now = time.time()
        with self._log_lock:
            if len(self._attempts) > 10000:  # many addresses: forget the quiet ones
                self._attempts = {k: t for k, t in self._attempts.items() if t and t[-1] > now - 60}
            times = [t for t in self._attempts.get(f"{route} {address}", []) if t > now - 60] + [now]
            self._attempts[f"{route} {address}"] = times
            over = len(times) > ATTEMPTS[route]
            alert = over and route not in NOT_ALERTED and self._refused(address, now)
        if alert and self.accounts is not None:
            self.accounts.log("lockout_alert", "", what="address", times=ALERT_AFTER, route=route)  # the address is the request's (Accounts.from_address)
        return over

    def _refused(self, address: str, now: float) -> bool:
        """An address was refused (under _log_lock): a new episode unless one began in the last minute. True when this one is the ALERT_AFTER-th of the
        last day (once a day per address)."""
        if len(self._refusals) > 10000:
            self._refusals = {a: t for a, t in self._refusals.items() if t and t[-1] > now - 86400}
        episodes = [t for t in self._refusals.get(address, []) if t > now - 86400]
        if episodes and episodes[-1] > now - 60:
            self._refusals[address] = episodes
            return False
        self._refusals[address] = episodes + [now]
        return len(episodes) + 1 == ALERT_AFTER

    def sign_in_start(self, name: str) -> tuple[str, str]:
        """(the provider's sign-in address, the state): a fresh state and nonce, good for one use within SIGN_IN_WINDOW.
        At most MAX_PENDING under way: beyond, the oldest is forgotten (its callback then says to try again)."""
        if self.accounts is None:
            raise LookupError("no staff accounts")
        provider = self._provider(name)
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        now = time.time()
        with self._log_lock:
            for old in [k for k, v in self._sign_ins.items() if v[2] <= now]:
                del self._sign_ins[old]
            while len(self._sign_ins) >= MAX_PENDING:
                del self._sign_ins[next(iter(self._sign_ins))]  # dicts keep insertion order: the oldest first
            self._sign_ins[state] = (name, nonce, now + SIGN_IN_WINDOW)
        return provider.authorize_url(state, nonce), state

    def sign_in_finish(self, state: str, code: str) -> tuple[str, dict]:
        """The provider sent the person back: the code becomes an ID token, checked (signature, issuer, audience,
        expiry, nonce, the firm's domain or tenant), and the email must be an active account on the staff list.
        LookupError: no such sign-in under way (or too old); SignInError: the token failed a check; ValueError:
        not on the staff list or turned off (the words of a wrong password)."""
        from connectors.signin import SignInError, staff_session

        with self._log_lock:
            pending = self._sign_ins.pop(state, None)
        if pending is None or pending[2] < time.time():
            raise LookupError("expired")
        name, nonce, _ = pending
        provider = self._provider(name)
        try:
            claims = provider.verify(provider.exchange(code), nonce)
        except SignInError as exc:
            self.accounts.log("sign_in_failed", "", reason=f"{name}: {exc}")
            raise
        except Exception as exc:  # noqa: BLE001 -- the provider unreachable, or an answer that isn't JSON
            self.accounts.log("sign_in_failed", "", reason=f"{name}: {type(exc).__name__}")
            raise SignInError(f"{name} didn't answer.") from None
        return staff_session(self.accounts, claims, name)

    def sign_in_cancel(self, state: str) -> None:
        with self._log_lock:
            self._sign_ins.pop(state, None)

    def sign_in_kind(self, state: str) -> str | None:
        """Which flow a state belongs to: a staff provider ("google", "microsoft") or "clio" (connecting the firm's Clio)."""
        with self._log_lock:
            pending = self._sign_ins.get(state)
        return pending[0] if pending else None

    # -- connections to the firm's other systems (Settings, Connections; connectors/clio.py) --

    @property
    def firm_data(self) -> Path:
        """The firm's data folder (data/), beside the client bundles: data/clio lives there."""
        return self.data_root.parent

    def connections(self, role: str | None) -> dict:
        """The Connections section: Clio in full, and Google Drive, Microsoft 365 and Filevine's state. The attorney's."""
        if role == "paralegal":
            raise PermissionError("Connections are the attorney's to see and change.")
        from connectors import clio

        return {"clio": clio.view(self.firm_data) | {"running": self._clio_running.locked()}, "others": clio.others()}

    def _drive_settings_controller(self, user: dict | None):
        self._attorneys_only(user, "Configuring selected Drive intake")
        email = self._upload_actor(user)  # canonical accounts, never request roles
        scope = self._prepared_source_scope()
        if self._staff_documents_root().absolute() != scope.documents:
            raise ValueError("Use this prepared installation's configured original-document root for Drive intake.")
        return drive_settings.Settings(scope, actor_reader=self._upload_actor_reader, may_access=restricted.visible_to), email

    def drive_settings(self, user: dict | None) -> dict:
        controller, email = self._drive_settings_controller(user)
        return controller.view(email)

    def drive_settings_save(self, body: dict, user: dict | None) -> dict:
        controller, email = self._drive_settings_controller(user)
        return controller.save(email, expected_revision=body.get("revision"), operation_id=body.get("attempt"),
                               supplied_provider=body.get("provider"), supplied_bindings=body.get("bindings"))

    def drive_settings_audit(self, body: dict, user: dict | None) -> dict:
        controller, email = self._drive_settings_controller(user)
        return controller.retry_audit(email, body.get("revision"))

    def _drive_case_context(self, client_id: str, user: dict | None):
        email = self._upload_actor(user)
        actor = self._upload_actor_reader(email)
        if (not isinstance(actor, dict) or actor.get("email") != email or actor.get("active") is not True
                or actor.get("role") not in ("attorney", "paralegal")):
            raise PermissionError("Sign in with a current active staff account before using selected Drive intake.")
        if not self.may_open(actor, client_id):
            raise LookupError("unknown case")
        scope = self._prepared_source_scope()
        if self._staff_documents_root().absolute() != scope.documents:
            raise ValueError("Use this prepared installation's configured original-document root for Drive intake.")
        return scope, email

    def drive_selection(self, client_id: str, user: dict | None) -> dict:
        """Only this authorized case's configured IDs; no provider discovery."""
        import source_association
        scope, _ = self._drive_case_context(client_id, user)
        current = drive_settings.read_record(scope)
        mapping = current["intake_mapping"]["bindings"] if current else {}
        selected = sorted(remote for remote, case in mapping.items() if case == client_id)
        result = {"client": client_id, "revision": current["revision"] if current else 0,
                  "selected": selected, "configured": bool(selected), "ready": False,
                  "setup_required": False, "audit_pending": bool(current and current["operation"]["audit"]["state"] == "pending")}
        if not selected:
            return result | {"reason": "An attorney must configure this case's selected Drive folder in Settings, Connections."}
        try:
            scope.case(client_id)
        except (ValueError, OSError):
            folder = scope.cases / client_id
            setup = source_association.hold(folder) or not (folder / "fact_graph.json").is_file()
            return result | {"setup_required": setup,
                             "reason": "Complete Setup or Recover sources before previewing selected Drive documents." if setup
                             else "This case's current source or lifecycle state is unavailable for selected Drive intake. Check Documents before retrying."}
        return result | {"ready": True, "reason": "Only the configured folder for this case will be previewed; nothing is written to Drive."}

    def _drive_case_mapping(self, client_id: str, body: dict, user: dict | None):
        scope, email = self._drive_case_context(client_id, user)
        scope.case(client_id)  # current lifecycle and complete original association
        current = drive_settings.read_record(scope)
        configured = current["intake_mapping"]["bindings"] if current else {}
        selected = body.get("selected")
        if (not isinstance(selected, list) or not selected or len(selected) > drive_intake.MAX_SELECTED
                or any(not isinstance(remote, str) or configured.get(remote) != client_id for remote in selected)
                or len(set(selected)) != len(selected)):
            raise ValueError("Choose a nonempty selection configured for this current case; reload its Drive selection if it changed.")
        return scope, email, {remote: client_id for remote in selected}

    def drive_preview(self, client_id: str, body: dict, user: dict | None) -> dict:
        scope, email, mapping = self._drive_case_mapping(client_id, body, user)
        # The installed helper owns current account/ACL/configuration and the
        # read-only source. A body-supplied mapping/root is never consumed.
        return drive_intake.installed(scope).preview(email, body["selected"], mapping)

    def drive_enqueue(self, client_id: str, body: dict, user: dict | None) -> dict:
        preview = body.get("preview")
        snapshot = preview.get("snapshot") if isinstance(preview, dict) else None
        if not isinstance(snapshot, dict):
            raise ValueError("Preview this case's configured selection before processing it.")
        scope, email, mapping = self._drive_case_mapping(client_id, {"selected": snapshot.get("selected")}, user)
        if snapshot.get("mapping") != mapping:
            raise ValueError("The displayed preview is not solely for this current case; preview its selection again.")
        # Delegated locks finish before the worker wake. No outer route lock is
        # held through provider preview or this helper's own case/submit locks.
        return drive_intake.installed(scope).enqueue(email, preview, body.get("attempt"),
            lambda current: self._wake_upload_worker(current.cases, current.portal, current.queue))

    def connections_save(self, body: dict, role: str | None) -> dict:
        """save | options | test | sync | disconnect | allow | hold: the attorney's buttons in Connections."""
        if role == "paralegal":
            raise PermissionError("Connections are the attorney's to change.")
        from connectors import clio

        who, action = str(body.get("reviewer") or "").strip(), str(body.get("action") or "")
        if not who:
            raise ValueError("Enter your name first: every change records who made it.")
        out: dict = {}
        try:
            if action == "save":
                clio.save_settings(self.firm_data, body.get("values") or {}, who)
            elif action == "options":  # Clio's practice areas, users, contact fields and calendars, for the mapping
                lists = clio.Clio(self.firm_data, transport=self.clio_transport).options()
                clio._update_state(self.firm_data, lambda s: s.__setitem__("options", lists))
            elif action == "test":
                ok, lines = clio.check(self.firm_data, transport=self.clio_transport)
                out = {"test": {"ok": ok, "lines": lines}}
            elif action == "sync":
                why = clio.ready(self.firm_data)
                if why:
                    raise ValueError(f"Clio can't sync yet: {why}.")
                if not self._clio_running.acquire(blocking=False):
                    raise ValueError("A Clio sync is already running.")
                threading.Thread(target=self._clio_sync, daemon=True, name="clio-sync").start()
                out = {"started": True}
            elif action in ("webhook_on", "webhook_off", "webhook_check"):  # Clio's webhooks: Subscribe, Stop, Check (src/connectors/clio_hooks.py)
                from connectors import clio_hooks

                if action == "webhook_on":
                    clio_hooks.subscribe(self.firm_data, who, transport=self.clio_transport)
                elif action == "webhook_off":
                    clio_hooks.stop(self.firm_data, who, transport=self.clio_transport)
                else:
                    clio_hooks.check(self.firm_data, transport=self.clio_transport)
            elif action == "disconnect":
                clio.disconnect(self.firm_data, who, transport=self.clio_transport)
            elif action in ("allow", "hold"):
                if role is None and self.accounts is not None:
                    raise PermissionError("Sign in first.")
                clio.allow(self.firm_data, str(body.get("case") or ""), who, allowed=action == "allow")
            else:
                raise ValueError("Not a Connections action.")
        except (clio.ClioError, httpx_errors()) as exc:
            raise ValueError(str(exc) if isinstance(exc, clio.ClioError) else "Clio couldn't be reached. Try again in a minute.") from None
        return self.connections(role) | out

    # -- Clio's webhooks (src/connectors/clio_hooks.py) and what went to Clio, per case (src/connectors/clio_sent.py) --

    def clio_webhook(self, body: bytes, signature: str | None, hook_secret: str | None) -> tuple[str, dict]:
        """A call to /clio/webhook, already size-checked: ("event" | "handshake" | "refuse", headers to answer with)."""
        from connectors import clio_hooks

        return clio_hooks.receive(self.firm_data, body, signature, hook_secret, wake=self.clio_wake, confirm=self.clio_confirm)

    def clio_wake(self) -> None:
        """An event named a linked matter: a "clio" job (src/jobs.py) reads what is waiting clio_hourly_delay seconds from now (a batch of uploads is one
        read), when an attorney switched that on. One job waits at a time: two events at once make one. The job worker runs it in its own process, never
        beside the overnight run's Clio step, Sync now or Send now (clio.step_lock)."""
        from connectors import clio

        if not clio.settings(self.firm_data).get("uploads_hourly"):
            return
        jobs.submit_once(self.jobs_root, "clio", by="Clio", args={"not_before": time.time() + self.clio_hourly_delay})

    def clio_confirm(self) -> None:
        """Clio's handshake came after Subscribe had returned (or someone else sent one): ask Clio, through the queue, which subscription is enabled and
        keep the secret that is Clio's (clio_hooks.check). One at a time, on its own thread: the handshake's answer is not kept waiting."""
        def run():
            from connectors import clio_hooks

            try:
                clio_hooks.check(self.firm_data, transport=self.clio_transport)
            except Exception as exc:  # noqa: BLE001 -- the person presses Check; the console says why
                sys.stderr.write(f"Clio webhook: the confirmation could not be read from Clio ({type(exc).__name__}).\n")
            finally:
                self._clio_confirming.release()
        if self._clio_confirming.acquire(blocking=False):
            threading.Thread(target=run, daemon=True, name="clio-confirm").start()

    def clio_resume(self) -> None:
        """At start: events that arrived before a restart are still waiting; read them within the hour."""
        from connectors import clio_hooks

        if clio_hooks.waiting(self.firm_data):
            self.clio_wake()

    def clio_case(self, client_id: str, role: str | None) -> dict | None:
        """The case page's "In Clio" line (src/connectors/clio_sent.py), or None when this case is not a Clio matter."""
        from connectors import clio_sent

        return clio_sent.case_view(self.firm_data, self.client_dir(client_id), client_id, role != "paralegal")

    def clio_send(self, client_id: str, body: dict, role: str | None) -> dict:
        """"Send now" on the case page: this case's packet, stage, deadlines, tasks and end state go to its Clio matter now (the sync's own checks, for this
        case alone). A protected case: an attorney's press is "Send to Clio" (the allowance Settings, Connections gives, recorded with who and when), a paralegal's
        is refused."""
        from connectors import clio

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: every change records who made it.")
        d = self.client_dir(client_id)
        st = clio.state(self.firm_data)
        if not any(m.get("case") == client_id for m in (st.get("matters") or {}).values()):
            raise LookupError("This case is not a Clio matter.")
        why = clio.ready(self.firm_data)
        if why:
            raise ValueError(f"Clio can't send yet: {why}.")
        if not (d / "fact_graph.json").exists():
            raise ValueError("This case has not been read yet: there is nothing to send.")
        held = clio.held_back(d, client_id, st)
        if held and (role == "paralegal" or (role is None and self.accounts is not None)):
            raise PermissionError("Only an attorney sends a protected case to Clio.")
        # the locks first (this process's, then the overnight run's): a "try again" leaves nothing allowed; the allowance is recorded once the send is queued
        if not self._clio_running.acquire(blocking=False):
            raise ValueError("A Clio sync is running now. Try again in a few minutes.")
        step = clio.step_lock(self.firm_data)
        try:
            step.__enter__()
        except clio.Busy:
            self._clio_running.release()
            raise ValueError("A Clio sync is running now (the overnight run's). Try again in a few minutes.") from None
        try:
            if held:
                clio.allow(self.firm_data, client_id, who)
            events.record("imports", "sent_now", "Pressed Send now to send this case to Clio", case_dir=d, who=who, role=role if role in ("attorney", "paralegal") else None)
            threading.Thread(target=self._clio_send_case, args=(client_id, who, role, step), daemon=True, name="clio-send").start()
        except BaseException:
            step.__exit__(None, None, None)
            self._clio_running.release()
            raise
        return {"started": True}

    def _clio_send_case(self, client_id: str, who: str, role: str | None, step) -> None:
        from connectors import clio

        try:
            with events.acting(who, role if role in ("attorney", "paralegal") else None, "staff"):
                client = clio.Clio(self.firm_data, transport=self.clio_transport)
                clio.sync_out(self.firm_data, self.data_root, client, only={client_id}, by_hand={"by": who, "at": clock.stamp()})
        except clio.ClioError as exc:
            clio._error(self.firm_data, str(exc), case=client_id)
        except Exception:  # noqa: BLE001 -- a send started from the page reports on the page, never kills the server
            import traceback

            traceback.print_exc()
            clio._error(self.firm_data, "The send stopped unexpectedly. The overnight run sends it; if it keeps stopping, ask your IT to look at the server's log.", case=client_id)
        finally:
            step.__exit__(None, None, None)
            self._clio_running.release()

    def _clio_sync(self) -> None:
        from connectors import clio

        try:
            # the client folders the overnight run last mirrored into, else the default beside data/ (overnight.py --clients-root)
            root = clio.state(self.firm_data).get("clients_root") or os.environ.get("I485_CLIENTS_ROOT") or self.firm_data.parent / "clients"
            with clio.step_lock(self.firm_data):
                clio.run(self.firm_data, Path(root), self.data_root, self.portal_root, transport=self.clio_transport)
        except clio.Busy:
            clio._error(self.firm_data, "The overnight run's Clio sync is running: this one was not started. It will be done by morning.")
        except Exception:  # noqa: BLE001 -- a sync started from the page reports on the page, never kills the server
            import traceback

            traceback.print_exc()  # the detail on the server's console; the page says it in words
            clio._error(self.firm_data, "The sync stopped unexpectedly. It runs again tonight; if it keeps stopping, ask your IT to look at the server's log.")
        finally:
            self._clio_running.release()

    def clio_connect_start(self, who: str) -> tuple[str, str]:
        """"Connect to Clio": Clio's consent screen, with a one-use state (the same pending list and window as staff
        sign-in: SIGN_IN_WINDOW, MAX_PENDING); the nonce slot holds who asked."""
        from connectors import clio

        state = secrets.token_urlsafe(24)
        url = clio.authorize_url(self.firm_data, state)
        now = time.time()
        with self._log_lock:
            for old in [k for k, v in self._sign_ins.items() if v[2] <= now]:
                del self._sign_ins[old]
            while len(self._sign_ins) >= MAX_PENDING:
                del self._sign_ins[next(iter(self._sign_ins))]
            self._sign_ins[state] = ("clio", who, now + SIGN_IN_WINDOW)
        return url, state

    def clio_connect_finish(self, state: str, code: str, error: str | None) -> str:
        """Clio sent the attorney back: the outcome word for the page (connected, declined, expired, failed)."""
        from connectors import clio

        with self._log_lock:
            pending = self._sign_ins.pop(state, None)
        if pending is None or pending[0] != "clio" or pending[2] < time.time():
            return "expired"
        if error:
            return "declined"
        try:
            clio.connect(self.firm_data, code, pending[1], transport=self.clio_transport)
        except (clio.ClioError, httpx_errors()) as exc:
            clio._error(self.firm_data, f"Connecting to Clio failed: {exc}" if isinstance(exc, clio.ClioError) else "Connecting to Clio failed: Clio couldn't be reached.")
            return "failed"
        return "connected"

    # -- API ------------------------------------------------------------------

    def case_list(self, user: dict | None, query: dict | None = None) -> dict:
        """Bounded cached assignments; fresh detail remains authoritative."""
        if self.accounts is None or not isinstance(user, dict) or not user.get("email"):
            raise PermissionError("Sign in with an active staff account to use My cases.")
        return self.roster.assignment_page(user, self.accounts.users, query)

    def _assignment_service(self, user: dict | None):
        if self.accounts is None or not isinstance(user, dict) or not user.get("email"):
            raise PermissionError("Sign in with an active staff account to manage assignment.")
        return case_assignment.Assignments(self.data_root, self.jobs_root, self.accounts.users)

    def _assignment_person(self, person: dict | None) -> dict | None:
        if person is None:
            return None
        return {"id": self.person_id(person["email"]), "name": person["name"], "role": person["role"]}

    def assignment_detail(self, client_id: str, user: dict | None) -> dict:
        """Case-authorized history and current eligible targets, never emails."""
        service = self._assignment_service(user)
        with self.case_write(client_id):
            # One fresh account snapshot under the case lock. Account-file
            # changes use their own lock; this is not a transaction across files.
            accounts = service._accounts()
            service.accounts = lambda: list(accounts.values())
            try:
                value = service.view(client_id, user["email"])
            except PermissionError as exc:
                raise LookupError("unknown client") from exc
            folder = service._folder(client_id)
            try:
                service._open(folder)
                can_manage = True
            except case_assignment.Conflict:
                can_manage = False
            eligible = []
            if can_manage:
                for email in accounts:
                    try:
                        person = service._eligible(email, accounts, folder)
                    except PermissionError:
                        continue
                    eligible.append(self._assignment_person(person))
            history = [{"revision": row["revision"], "action": row["action"],
                        "actor": self._assignment_person(row["actor"]),
                        "before": self._assignment_person(row["before"]),
                        "after": self._assignment_person(row["after"]),
                        "reason": row["reason"], "at": row["at"], "audit": row["audit"]}
                       for row in value["history"]]
            return {"state": value["state"], "revision": value["revision"],
                    "assignee": self._assignment_person(value["assignee"]),
                    "history": history, "eligible_assignees": eligible,
                    "can_manage": can_manage,
                    "can_claim": can_manage and value["state"] == "unassigned",
                    "audit_pending": any(row["audit"]["state"] != "recorded" for row in value["history"])}

    def assignment_change(self, client_id: str, body: dict, user: dict | None) -> dict:
        service = self._assignment_service(user)
        with self.case_write(client_id):
            accounts = service._accounts()
            service.accounts = lambda: list(accounts.values())
            target = None
            chosen = body.get("assignee")
            action = body.get("action")
            if action == "reassign":
                # No legacy email fallback. The current eligible target is
                # resolved by public id and rechecked by change() under lock.
                matches = [email for email, person in accounts.items()
                           if person.get("active") is True and person.get("role") in case_assignment.ROLES
                           and isinstance(chosen, str) and chosen == self.person_id(email)]
                if len(matches) != 1:
                    raise ValueError("Choose a current eligible staff member.")
                target = matches[0]
            elif chosen is not None:
                raise ValueError("Claim or clear does not select another assignee.")
            result = service.change(client_id, user["email"], action,
                                    expected_revision=body.get("revision"), operation_id=body.get("operation_id"),
                                    target_email=target, reason=body.get("reason", ""))
            self.roster.touch(client_id)
            return {**result, "assignee": self._assignment_person(result["assignee"]),
                    "operation_assignee": self._assignment_person(result["operation_assignee"])}

    def clients(self, user: dict | None = None) -> list[dict]:
        """The "Choose a client" list: the cases this person may see (src/restricted.py)."""
        who = user if self.accounts is not None else None
        if self.scaled:  # one line for each case this person may see, from the roster (the line's counts are the ones read when the case was last read)
            return self.roster.picks(who)
        out = []
        for d in sorted(p for p in self.data_root.iterdir() if (p / "fact_graph.json").exists()):
            if not restricted.visible_to(who, d):
                continue
            report = (d / "flag_report.txt").read_text(encoding="utf-8") if (d / "flag_report.txt").exists() else ""
            counts = {}
            for level in ("BLOCKING", "REVIEW"):
                marker = report.find(f"{level} (")
                counts[level.lower()] = int(report[marker + len(level) + 2: report.find(")", marker)]) if marker >= 0 else None
            name, kind = self._name_and_kind(d)
            out.append({"id": d.name, "name": name, "kind": kind, "decisions": len(load_decisions(d)), **counts, "restricted": restricted.is_restricted(d)})
        return out

    @staticmethod
    def _name_and_kind(d: Path) -> tuple[str | None, str | None]:
        """The client's name and kind of case for the "Choose a client" list, from the rows the dashboard already cached in the case
        folder (overview.json, journey_summary.json): nothing is rebuilt for 1,800 cases. None until the dashboard has built the row."""
        from review.overview import name_and_kind

        return name_and_kind(d)

    def _rows(self, who: dict | None, *, with_progress: bool = False):
        """Every case this person may see (src/restricted.py), with its office: the rows behind All clients, My work, What's due
        and Reports. A confidential document's deadline is still on its row (review/expiring.py counts those it leaves out)."""
        from review.overview import overview

        import offices

        if self.scaled:  # the roster's rows: restricted marks and offices are in them already (review/roster.py)
            if with_progress:
                rows, pending = self.roster.rows(who, with_progress=True)
                return self._purge_marks(rows, who), pending
            return self._purge_marks(self.roster.rows(who), who)
        import engagement

        rows = self._visible_rows(overview(self.data_root, self.field_map, self.template, self.catalog, self.portal_root)["clients"], who)
        for row in rows:
            d = self.data_root / row["id"]
            if (d / "fact_graph.json").exists():
                row["office"] = offices.for_case(d, (row.get("summary") or {}).get("state"))["name"]
            ended = engagement.end_info(d) or engagement.conflict_declined(d)  # declined, withdrawn, transferred, closed, or declined after the
            if ended:  # conflict search (src/engagement.py, src/conflicts.py): off every work list, under All clients' filter
                row["end"] = ended
        rows = self._purge_marks(rows, who)
        return (rows, 0) if with_progress else rows

    def _purge_marks(self, rows: list[dict], who: dict | None) -> list[dict]:
        """An attorney's rows say "to be purged on MM/DD/YYYY" for a case whose purge waits (src/purge.py); nobody else's say anything of a purge."""
        if who is not None and who.get("role") != "attorney":
            return rows
        import purge

        waiting = purge.waiting(self.data_root)
        return [r | {"purge_on": waiting[r["id"]]["purge_on"]} if r.get("id") in waiting else r for r in rows] if waiting else rows

    @staticmethod
    def _open_rows(rows: list[dict]) -> list[dict]:
        """The cases still open: an ended case leaves every work list and every count of work (src/engagement.py)."""
        return [r for r in rows if not r.get("end")]

    PAGE = 50  # rows on a page of All clients and of What's due
    QUIET = 7  # days without anything happening that the "Quiet for 7+ days" filter means
    ORDER = ("review", "attorney", "processing", "answering", "invited", "not_invited", "ready", "filed")  # the order All clients lists the stages in

    @staticmethod
    def _page_of_list(items: list, q: dict, size: int = 50) -> tuple[list, dict]:
        """One page of a list (?page=, ?size=, at most 200 a page): the rows, and where they are ({total, page, pages, size})."""
        try:
            size = max(1, min(int(q.get("size") or size), 200))
            page = max(1, int(q.get("page") or 1))
        except (TypeError, ValueError):
            raise ValueError("Page and size must be numbers.") from None
        pages = max(1, -(-len(items) // size))
        page = min(page, pages)
        return items[(page - 1) * size: page * size], {"total": len(items), "page": page, "pages": pages, "size": size}

    def overview(self, role: str | None = None, user: dict | None = None, q: dict | None = None) -> dict:
        """All clients: one page (50 rows) of the cases this person may see, filtered and put in order here (?stage=, ?office=, ?blocked=1, ?idle=1, ?messages=1, ?q=
        the words of a name or an id, ?page=), with how many match and the counts by stage over every case they may see: a restricted case they may not open is in no
        page and no count (src/restricted.py). Each row carries its own deadlines ("due"). ?counts=1 gives the counts alone."""
        from review.overview import STAGE_NAMES, STAGES, deadlines

        from overnight import progress

        # the overnight run's state lives next to the client bundles (data/)
        import offices

        q = q or {}
        who = self._who(role, user)
        rows, pending = self._rows(who, with_progress=True)
        rows = self._unrestricted(rows, who)
        working = self._open_rows(rows)  # the stages and deadlines count the open cases; an ended one is listed under All clients' filter
        every = offices.offices()
        from portal.bank import language_names

        batch = progress(self.data_root.parent)
        if batch and who is not None and who.get("role") != "attorney":  # the folders running now, by name: none for a list that hides cases
            batch = batch | {"running": []}
        quiet = lambda r: (r.get("idle_days") or 0) >= self.QUIET and r.get("stage") not in ("filed", "ready")  # noqa: E731
        by_stage = Counter(r.get("stage") for r in working)
        out = {"stages": [{"id": s, "name": STAGE_NAMES[s], "count": by_stage.get(s, 0)} for s in STAGES],
               "counts": {"total": len(rows), "blocking": sum(1 for r in rows if r.get("blocking")), "quiet": sum(1 for r in rows if quiet(r)),
                          "messages_on": sum(1 for r in rows if r.get("messages_on")),
                          "due_soon": sum(1 for d in deadlines(working + self.prospect_tasks(user)) if d["level"] in ("overdue", "urgent"))},
               "batch": batch, "ended": len(rows) - len(working), "offices": [o["name"] for o in every] if len(every) > 1 else [], "language_names": language_names(),
               "still_reading": pending}  # progress belongs to the same row snapshot, even if the background walk completes meanwhile
        if q.get("counts"):
            return out
        needle = str(q.get("q") or "").strip().lower()
        ended = q.get("ended") if q.get("ended") in ("open", "ended", "all") else "open"  # the open cases, the ended ones (closed, declined, withdrawn, transferred) or every case
        kept = [r for r in rows if (ended == "all" or (ended == "ended") == bool(r.get("end"))) and (not q.get("stage") or r.get("stage") == q["stage"])
                and (not q.get("office") or r.get("office") == q["office"])
                and (not q.get("blocked") or r.get("blocking")) and (not q.get("messages") or r.get("messages_on")) and (not q.get("idle") or quiet(r))
                and (not needle or needle in f"{r['id']} {(r.get('summary') or {}).get('name') or ''}".lower())]
        kept.sort(key=lambda r: (-(r.get("blocking") or 0), self.ORDER.index(r["stage"]) if r.get("stage") in self.ORDER else -1, -(r.get("idle_days") or 0)))
        page, where = self._page_of_list(kept, q, self.PAGE)
        due: dict[str, list] = {}
        for d in deadlines([r for r in page if not r.get("end")]):  # a case that has ended has no deadlines on its row
            due.setdefault(d["client"], []).append(d)
        return out | where | {"clients": [{k: v for k, v in r.items() if not k.startswith("_")} | {"due": due.get(r["id"], [])} for r in page]}

    def deadlines_page(self, role: str | None, user: dict | None, q: dict) -> dict:
        """What's due, Deadlines: every deadline of the cases this person may see, late ones first, then the next 60 days, a page at a time (?page=)."""
        from review.overview import deadlines

        who = self._who(role, user)
        # the open prospects' tasks are on What's due beside the cases' deadlines (src/prospects.py work_rows); a prospect is in no other list or count
        items = deadlines(self._open_rows(self._unrestricted(self._rows(who), who)) + self.prospect_tasks(user))
        page, where = self._page_of_list(items, q, self.PAGE)
        return where | {"items": page, "late": sum(1 for d in items if d["level"] == "overdue")}

    def maintenance(self) -> dict:
        """What needs keeping up to date (src/maintenance.py) -- the dashboard shows the count."""
        import maintenance

        # the items the firm changes on the Settings page link there (src/settings.py)
        where = {"visa_bulletin": "visa_bulletin_eb4", "visa_bulletin_family": "visa_bulletin_family", "fee_schedule": "fees", "eoir_fees": "fees",
                 "poverty_guidelines": "poverty", "payment_rules": "payment", "firm_details": "firm", "clio_app_credentials": "connections",
                 "clio_webhooks": "connections"}
        import deployment

        about = deployment.about()
        name = about["provider"]["name"]
        mine, theirs = [], []
        for i in maintenance.status():
            if i["responsible"] == "firm":
                mine.append({"id": i["id"], "what": (i["firm_what"] or i["what"]).replace("{provider}", name), "owner": "Your IT" if i["party"] == "host" else i["owner"].title(),
                             "cadence": i["cadence"], "last_checked": i["last_checked"], "checked_by": i["checked_by"], "due": i["due"],
                             "findings": [maintenance.public_finding(f) for f in i["findings"]], "settings": where.get(i["id"]),
                             "state": i["state"], "status": i["status"], "check_type": i["check_type"],
                             "responsible": i["responsible"], "required_role": i["owner"],
                             "live_check": i["live_check"] | {"finding": maintenance.public_finding(i["live_check"]["finding"]) if i["live_check"]["finding"] else None},
                             "steps": [s.replace("{provider}", name) for s in i["firm_steps"]]})
            else:  # Provider responsibility and recorded evidence, never invented progress.
                theirs.append({"id": i["id"], "what": (i["firm_what"] or i["what"]).replace("{provider}", name),
                               "cadence": i["cadence"], "last_checked": i["last_checked"],
                               "status": i["status"], "state": i["state"], "due": i["due"], "check_type": i["check_type"],
                               "findings": [maintenance.public_finding(f) for f in i["findings"]],
                               "responsible": i["responsible"], "required_role": i["owner"],
                               "live_check": i["live_check"] | {"finding": maintenance.public_finding(i["live_check"]["finding"]) if i["live_check"]["finding"] else None}})
        import case_status

        # USCIS's case status: switched on on this server or not, and the last night's check (src/case_status.py), under its line
        state = case_status.state_text(self.data_root.parent)
        for x in mine + theirs:
            if x["id"] == "uscis_case_status":
                x["note"] = state
        import backups

        for x in mine + theirs:  # the last backup and the last test restore, and the last restore drill, from the backup log (src/backups.py)
            if x["id"] == "backups":
                x["note"] = backups.status()["line"]
            if x["id"] == "restore_drill":
                shown = backups.drill_status()
                x["note"], x["note_bad"] = shown["line"], bool(shown["last"] and not shown["last"].get("ok"))
        live = maintenance.recorded_live()
        # a line per connection (connectors/clio.py): Clio is the firm's own account; the others' keys are on the server (IT)
        from connectors import clio

        line, needs = clio.state_text(self.firm_data)
        for x in mine:
            if x["id"] == "clio_app_credentials" and not needs:  # when it needs the firm, the same words are the item's finding
                x["note"] = line
            if x["id"] == "clio_webhooks":  # where the subscription stands, in the words Settings uses
                from connectors import clio_hooks

                x["note"] = clio_hooks.view(self.firm_data)["line"]
        connections = [{"name": "Clio", "who": "The firm", "state": line, "attention": needs}] + [
            {"name": o["name"], "who": "Your IT", "state": o["state"], "attention": False} for o in clio.others()]
        # form editions USCIS has changed: the packets that use them wait (src/editions.py); whose job it is to update them
        import editions

        results = live.get("results") or {}
        held = []
        for i in maintenance.registry()["items"]:
            result = results.get(i["id"])
            v = editions.verdict(result) if i["check"]["type"] == "uscis_form_edition" and isinstance(result, dict) else None
            if v:
                held.append(v | {"id": i["id"], "updating": editions.updating(name, deployment.DEFAULT["provider"]["name"])})
        for x in theirs:  # the provider's row says what changed, in the firm's words
            v = next((h for h in held if h["id"] == x["id"]), None)
            if v:
                x["note"] = v["headline"] + " " + v["updating"]
        return {"items": mine, "provider_items": theirs, "about": about, "due": sum(1 for i in mine if i["due"]), "connections": connections, "held": held,
                "live_checked_at": live.get("at") if isinstance(live.get("at"), str) and
                clock.local_date(live["at"]) is not None and clock.local_date(live["at"]) <= clock.today() else None}

    def settings(self, role: str | None) -> dict:
        """The Settings page (src/settings.py): every section, its values, who changed it last."""
        import settings

        sections = settings.specs()
        import case_questions
        import drafting

        import g28

        for s in sections:  # what the model on this machine is told and may do, in full, with the attorney's approval of each (rules/approval.py)
            if s["id"] == "drafting":
                s["practices"] = [drafting.practice() | {"name": "Drafting the client's declaration"}, case_questions.practice()]
            elif s["id"] == "firm" or s.get("office"):  # the offices' G-28 choices: one approval holds for every office's choices as they stand (src/g28.py)
                s["practices"] = [g28.practice()]
        if role != "paralegal":  # the firm's other systems: the attorney's alone (connectors/clio.py)
            c = self.connections(role)
            sections.append({"id": "connections", "title": "Connections", "cadence": "When the firm decides", "fields": [], "links": [],
                             "help": "The firm's other systems. Clio, once connected: its matters, clients and documents come in every night; the filing "
                                     "packet, the review bundle, each mailing record, the case's stage and every deadline go back to the matter. Nothing is "
                                     "ever deleted on either side.",
                             "unproven": CLIO_UNPROVEN,
                             "connections": c, "updated_by": c["clio"]["updated_by"], "updated_at": c["clio"]["updated_at"]})
        import version

        # what each release changed (docs/releases.md), under the version number: read-only, for everyone
        import ledger_seal

        # whether the event ledger is as it was written, from the overnight run's last check (a view never walks the ledger)
        out = {"sections": sections, "can_edit": role != "paralegal", "releases": version.releases(),
               "record": ledger_seal.settings_line(events.base_path(self.data_root.parent))}
        if role != "paralegal":  # the attorney's: where each key lives, when it was changed and by whom, when it is due; never a value, never a fragment (src/firmsecrets.py)
            import firmsecrets

            rows = firmsecrets.status(data_root=self.data_root.parent, key_path=self.accounts.key_path if self.accounts is not None else None)
            out["secrets"] = [r | {"due_on": clock.us_date(r["due_on"].isoformat()) if r["due_on"] else None} for r in rows]
        return out

    def settings_save(self, body: dict, role: str | None) -> dict:
        import settings

        if role == "paralegal":
            raise PermissionError("Settings are the attorney's to change. You can read them here.")
        if "values" in body and not isinstance(body["values"], dict):  # a list of values would stop the request half-way (unhashable names)
            raise ValueError("Send each setting with its name and value. Reload the page and try again.")
        if body.get("section") and isinstance(body.get("values"), dict):  # the Staff section's own choices: only there (logged, and acted on)
            staff_only = {f["key"] for sec in settings.specs() if sec["id"] == body["section"] for f in sec["fields"] if f.get("staff")}
            if staff_only & set(body["values"]):
                raise ValueError("Who must use a code, and remembering a computer, are set on the Staff section.")
        with self._lock:
            if body.get("action") == "add_office":
                settings.add_office(str(body.get("reviewer") or ""))
            elif body.get("action") == "add_translator":  # who may sign a certificate of translation (src/translation.py)
                settings.add_translator(str(body.get("reviewer") or ""), str(body.get("name") or ""), list(body.get("languages") or []),
                                        str(body.get("kind") or ""), str(body.get("competence") or ""), str(body.get("organization") or ""),
                                        str(body.get("address") or ""))
            elif body.get("action") == "remove_translator":
                settings.remove_translator(str(body.get("translator") or ""), str(body.get("reviewer") or ""))
            elif body.get("action") == "secret_cadence":  # how often the attorney wants a kind of key changed (src/firmsecrets.py): no value is ever sent or kept here
                import firmsecrets

                firmsecrets.set_cadence(str(body.get("kind") or ""), str(body.get("cadence") or ""), str(body.get("reviewer") or "").strip() or "an attorney", data_root=self.data_root.parent)
            elif body.get("action") == "remove_office":
                import offices

                oid = str(body.get("section") or "")
                using = [d.name for d in self.data_root.iterdir() if (offices.chosen(d) or {}).get("office") == oid]
                if using:
                    raise ValueError(f"{len(using)} case(s) are filed from this office ({', '.join(using[:3])}"
                                     f"{'…' if len(using) > 3 else ''}). Move them to another office first.")
                settings.remove_office(oid, str(body.get("reviewer") or ""))
            else:
                settings.save(str(body.get("section") or ""), body.get("values") or {}, str(body.get("reviewer") or ""))
        return self.settings(role)

    def maintenance_mark(self, body: dict) -> dict:
        import maintenance

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: every check records who did it.")
        item = next((i for i in maintenance.status() if i["id"] == str(body.get("id") or "")), None)
        if item is not None and item["id"] == "restore_drill":
            raise PermissionError("The restore drill records itself when it passes: run it instead.")
        if item is not None and item["responsible"] != "firm":
            raise PermissionError(f"This one is kept current by {__import__('deployment').provider()}, not by the firm.")
        with self._lock:
            maintenance.mark(str(body.get("id") or ""), who)
        return self.maintenance()

    # -- the visible trail (docs/design_plan.md Part 4) ------------------------------

    def rules(self) -> dict:
        """Every rule and firm policy: plain text, source, and the attorney's approval for every case (src/rules/approval.py)."""
        from review.state import rule_info
        from rules import approval

        return {"rules": [rule_info(r["id"]) for r in approval.catalog()]}

    def rules_approve(self, body: dict, role: str | None) -> dict:
        from rules import approval

        if role == "paralegal":
            raise PermissionError("Only an attorney approves a rule for every case. You can read it here.")
        with self._lock:
            approval.approve(str(body.get("rule") or ""), str(body.get("reviewer") or ""), role)
        return self.rules()

    def build_review_bundle(self, client_id: str, body: dict) -> dict:
        """The review bundle behind a built packet (src/review/bundle.py), from what the review already recorded."""
        from review import bundle

        from questionnaire.pages import page_images

        d = self.client_dir(client_id)
        with self._lock:  # the page images the review cards' crops use (page_image), of the client's own files only
            return bundle.build(d, body.get("filing") or None, str(body.get("reviewer") or ""),
                                lambda doc: page_images(self.source_snapshot(client_id, doc).data), lambda doc: self.source_file(client_id, doc))

    def accuracy(self, q: dict, role: str | None) -> dict:
        from review.learning import accuracy
        from review.state import concise

        if role == "paralegal":
            raise PermissionError("The accuracy record is the attorney's.")
        return accuracy(self.data_root, q.get("from") or None, q.get("to") or None,
                        lambda key: concise(self.catalog.label(key), 90), self.catalog.ref)

    def accuracy_references(self, user: dict | None = None) -> dict:
        """The Accuracy record's comparison with hand-filled references (src/accuracy.py): the nights' history in words, a sentence for
        any fall, last night's figures and the boxes that differ. The attorney's. A case this person may not open is counted, not listed."""
        import accuracy
        from review.state import concise, is_private_number, mask_number, tidy_tooltip

        if (user or {}).get("role") == "paralegal":
            raise PermissionError("The accuracy record is the attorney's.")
        out = accuracy.screen(self.data_root, lambda case: self.may_open(user, case))
        for d in out["different"]:
            key, tip = d.pop("key"), d["label"]
            if is_private_number(key, tip):  # a number the screen never prints in full, as on every review card
                d["reference"], d["ours"] = mask_number(d["reference"]), mask_number(d["ours"])
            if d["form"] == "i485" and key and self.catalog.ref(key):  # the I-485's own Part and Item for the box
                d["label"] = f"{self.catalog.ref(key)}: {concise(self.catalog.label(key), 90)}"
            else:  # another form: the Part its tooltip names, then the question
                tip = tidy_tooltip(tip)
                part = re.match(r"Part \d+", tip)
                d["label"] = (f"{part.group(0)}: " if part else "") + concise(tip, 90)
        return out

    def accuracy_mark(self, body: dict, user: dict | None = None) -> dict:
        """A person's mark that the hand-filled reference was the one that was wrong on one box: who, when and why are kept beside it."""
        import accuracy

        if (user or {}).get("role") == "paralegal":
            raise PermissionError("Only an attorney marks a reference as wrong.")
        case = str(body.get("case") or "")
        if not self.may_open(user, case):
            raise ValueError("That box is not one the last comparison found different.")  # the same answer for a case this person may not open
        with self._lock:
            mark = accuracy.mark(self.data_root, case, str(body.get("form") or ""), str(body.get("field") or ""), str(body.get("reason") or ""),
                                 str(body.get("reviewer") or ""))
        return {"ok": True, "mark": mark}

    def learning(self, user: dict | None = None) -> dict:
        from review.learning import learning
        from review.state import concise

        from learning.report import document_type_report

        hidden = self._scope(user if self.accounts is not None else None)["hidden"]  # a restricted case's corrections are nobody else's examples
        report = learning(self.data_root, lambda key: concise(self.catalog.label(key), 90), exclude=hidden)
        # shadow models (src/learning): how a local decision model would have done, never acted on
        shadow = document_type_report(self.data_root.parent / "learning.db")
        for m in shadow.get("models") or []:
            m["examples"] = [x for x in m.get("examples") or [] if x.get("client") not in hidden]
        return report | {"shadow": shadow}

    def ask_preview(self, client_id: str, body: dict) -> dict:
        """The question in the language the client reads the portal in, as the offline translator drafts it
        (portal/questions.py), for the paralegal to see and correct before it is added to the client's list."""
        from portal.questions import draft
        from portal.store import PortalStore

        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            raise ValueError("This client isn't in the client portal.")
        lang = PortalStore(self.portal_root).profile(client_id).get("language") or "pt"
        return draft(str(body.get("text") or ""), lang, body.get("options") if body.get("type") == "choice" else None)

    def ask_client(self, client_id: str, body: dict) -> dict:
        """'Ask the client': a question or a document, as a task in the client's
        portal, with a text/email carrying only a sign-in link (portal/notify.py).
        The question is typed (words, a date, Yes or No, a choice) and carries its
        translation into the client's language (portal/questions.py)."""
        from portal.notify import Notifier, delivery
        from portal.questions import request_fields
        from portal.store import PortalStore

        who = str(body.get("reviewer") or "").strip()
        text = str(body.get("text") or "").strip()
        if not who:
            raise ValueError("Enter your name first: the request records who asked.")
        if not text:
            raise ValueError("Write what the client should answer or send.")
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            raise ValueError(f"This client isn't in the client portal yet, so they can't be reached here. {__import__('deployment').support_start()} adds them to the portal.")
        store = PortalStore(self.portal_root)
        facts = [str(k) for k in body.get("facts") or []]
        why = self._decided_why(client_id)
        if facts and all(k in why for k in facts):  # the office already decided this card: the client is not asked again
            return {"skipped": True, "why": why[facts[0]]}
        typed = request_fields(body | {"text": text}, store.profile(client_id).get("language") or "pt")
        if body.get("queue"):  # into the client's list: one message later for everything (no texts one question at a time)
            request = store.add_request(client_id, text, body.get("doc_id") or None, who, draft=True, facts=facts, typed=typed)
            return {"request": request, "waiting": sum(1 for r in store.requests(client_id) if r["status"] == "draft")}
        request = store.add_request(client_id, text, body.get("doc_id") or None, who, facts=facts, typed=typed)
        sent = Notifier(self.portal_root / "outbox.jsonl", cases_root=self.data_root, store=store).send(store.profile(client_id), "request", self._sign_in_link(store, client_id))
        result = delivery(sent)  # what really happened: "queued" when no mail server is configured, never "sent"
        store.mark_delivery(client_id, [request["id"]], result)
        return {"request": request, "sent": sent, "delivery": result}

    def filing_ask(self, client_id: str, body: dict) -> dict:
        """A filing's long questions (the I-601's hardship, the I-212's removals) into the client's list, in the language the client
        reads the portal in, in the filing's own DRAFT wording (filing_questions.ask_in_portal). Each waits in the list like any
        "Ask the client" question until the office sends the list; one already waiting, asked or answered (its answer
        not yet in the case) isn't added twice."""
        import filing_questions
        from portal.store import PortalStore

        who = str(body.get("reviewer") or "").strip()
        filing = str(body.get("filing") or "")
        if not who:
            raise ValueError("Enter your name first: the request records who asked.")
        if not filing_questions.module(filing):
            raise ValueError(f"No questions for the filing {filing!r}.")
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            raise ValueError(f"This client isn't in the client portal yet, so they can't be reached here. {__import__('deployment').support_start()} adds them to the portal.")
        store = PortalStore(self.portal_root)
        try:
            folder = self.client_dir(client_id)
        except LookupError:  # invited, not processed yet: every question is still to ask
            folder = None
        asked = {k for r in store.requests(client_id) if r.get("status") in ("draft", "open", "answered") for k in r.get("facts") or []}
        lang = store.profile(client_id).get("language") or "pt"
        added = [store.add_request(client_id, text, None, who, draft=True, facts=[key], typed=typed)
                 for key, text, typed in filing_questions.ask_in_portal(filing, folder, lang) if key not in asked]
        return {"added": len(added), "waiting": sum(1 for r in store.requests(client_id) if r["status"] == "draft")}

    def ask_send(self, client_id: str, body: dict) -> dict:
        """Everything waiting in the client's list, in the portal at once, with one text or email."""
        from portal.notify import Notifier, delivery
        from portal.store import PortalStore

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: the message records who sent it.")
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            raise ValueError(f"This client isn't in the client portal yet, so they can't be reached here. {__import__('deployment').support_start()} adds them to the portal.")
        store = PortalStore(self.portal_root)
        skipped = store.skip_drafts(client_id, self._decided_why(client_id))  # decided on a card since it was added: not sent
        sent_requests = store.send_drafts(client_id, who)
        if not sent_requests and skipped:
            return {"count": 0, "skipped": len(skipped), "why": skipped[0]["skipped_why"], "sent": [], "delivery": None}
        if not sent_requests:
            raise ValueError("Nothing is waiting to be sent.")
        sent = Notifier(self.portal_root / "outbox.jsonl", cases_root=self.data_root, store=store).send(store.profile(client_id), "request", self._sign_in_link(store, client_id))
        result = delivery(sent)
        store.mark_delivery(client_id, [r["id"] for r in sent_requests], result)
        return {"count": len(sent_requests), "skipped": len(skipped), "sent": sent, "delivery": result}

    def _decided_why(self, client_id: str) -> dict[str, str]:
        """{fact key: why the client is not asked} for every fact the office has decided on a review card."""
        from portal.engine import _skip_record, decided_facts

        try:
            folder = self.client_dir(client_id)
        except LookupError:  # invited, not processed yet: nothing to have decided
            return {}
        return {key: _skip_record("", "", key, d)["why"] for key, d in decided_facts(folder).items()}

    def ask_drop(self, client_id: str, body: dict) -> dict:
        from portal.store import PortalStore

        if self.portal_root is None:
            raise LookupError("No client portal.")
        PortalStore(self.portal_root).drop_draft(client_id, str(body.get("id") or ""))
        return {"ok": True}

    def _sign_in_link(self, store, client_id: str) -> str:
        """Only central accepted dispatch creates a channel-bound credential."""
        return ""

    def remind(self, client_id: str, body: dict) -> dict:
        """'Send reminder': a fresh sign-in link to a client who hasn't finished
        the portal, on the channels they agreed to (no case details in it)."""

        from portal.notify import Notifier, delivery
        from portal.store import PortalStore

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: the reminder records who sent it.")
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            raise ValueError("This client isn't in the client portal.")
        store = PortalStore(self.portal_root)
        profile = store.profile(client_id)
        if profile.get("status") == "submitted":
            raise ValueError("This client already sent their questionnaire: nothing to remind them of.")
        sent = Notifier(self.portal_root / "outbox.jsonl", cases_root=self.data_root, store=store).send(profile, "reminder", self._sign_in_link(store, client_id))
        store.update_profile(client_id, last_reminder_attempt_at=clock.stamp(), last_reminder_attempt_by=who)
        if any(row.get("result") == "sent" for row in sent):
            store.update_profile(client_id, last_reminder_at=clock.stamp(), last_reminder_by=who)
            store.log(client_id, "reminded", {"by": who})  # the portal's own log, and the event ledger (portal/store.py _LEDGER)
        result = delivery(sent)
        store.log(client_id, "reminder", {"by": who, "channels": [s["channel"] for s in sent if s["result"] != "skipped"], "status": result["status"], "text": result["text"]})
        return {"sent": sent, "delivery": result}

    def answers_page(self, client_id: str) -> str:
        """The client's portal answers, readable (review/answers_page.py): what "Open" shows for "portal questionnaire"."""
        from portal.bank import bank_for
        from portal.store import PortalStore
        from review import answers_page

        # a known portal client only (one still answering has no case folder yet)
        store = PortalStore(self.portal_root) if self.portal_root is not None else None
        if store is None or client_id not in store.clients():
            raise LookupError("This client has no portal answers.")
        profile = store.profile(client_id)
        from review.state import display_name
        return answers_page.render(display_name(self.data_root / client_id, profile.get("name")), store.answers(client_id), bank_for(profile),
                                   profile.get("language") or "en", profile.get("submitted_at"), store.requests(client_id), client_id=client_id)

    def questionnaire_pdf(self, client_id: str) -> bytes:
        from portal.questionnaire_pdf import render
        store = self._portal()
        if client_id not in store.clients():
            raise LookupError("This client has no portal questionnaire.")
        with store._communication_gate(), store._lock:
            from portal.display_profile import current
            return render(current(store, client_id), store.answers(client_id))

    def client_requests(self, client_id: str) -> list:
        """The office's requests to the client, each with the answer in words and how it reached them (portal/questions.py)."""
        from portal.questions import asked_line, reply_words
        from portal.store import PortalStore

        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            return []
        return [r | {"reply_words": reply_words(r), "asked_line": asked_line(r)} for r in PortalStore(self.portal_root).requests(client_id)]

    # -- messages with the client (src/portal/messages.py) and photos waiting to be retaken ----------

    def _portal_client(self, client_id: str):
        """The portal store, for a client who is in the portal (a message or a retake belongs to the portal's folder)."""
        from portal.store import PortalStore

        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            raise ValueError("This client isn't in the client portal yet, so there are no messages.")
        return PortalStore(self.portal_root)

    def client_messages(self, client_id: str) -> dict:
        """The thread with the client, oldest first, and the photos the office asked them to retake (empty for a client not in the portal)."""
        from portal.bank import language_names

        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            return {"messages": [], "retakes": [], "language": None, "language_name": None}
        store = self._portal_client(client_id)
        lang = store.profile(client_id).get("language", "pt")
        out = []
        for m in store.messages(client_id):
            draft = (m.get("translations") or {}).get(lang) or {}
            out.append({k: m[k] for k in ("id", "from", "text", "at", "by", "status", "handled_by", "handled_at", "delivery", "seen_at") if k in m}
                       | ({"translation": {k: draft.get(k) for k in ("text", "machine", "needs_translator", "edited_by")}} if m["from"] == "office" and lang != "en" else {}))
        retakes = [{"document": t.get("doc_en") or t.get("doc_id"), "why": t.get("why_en") or "the photo could not be read", "asked_at": t.get("asked_at")}
                   for t in store.tasks(client_id) if t.get("kind") == "retake" and not t.get("received_at")]
        return {"messages": out, "retakes": retakes, "language": lang, "language_name": language_names().get(lang)}

    def message_preview(self, client_id: str, body: dict) -> dict:
        """The answer in the client's language as a machine draft, for the staff member to read and change before it is sent."""
        from portal.bank import language_names
        from portal.messages import translate_for_client

        store = self._portal_client(client_id)
        text = str(body.get("text") or "").strip()
        if not text:
            raise ValueError("Write the answer first.")
        lang = store.profile(client_id).get("language", "pt")
        return translate_for_client(text, lang) | {"language_name": language_names().get(lang)}

    def message_reply(self, client_id: str, body: dict) -> dict:
        """The office answers a client's message: into the portal thread, and "the office answered you, open your page" out by the
        channels the client agreed to (no word of the answer or the case in it)."""
        from portal import messages
        from portal.notify import Notifier

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: the answer records who sent it.")
        store = self._portal_client(client_id)
        sent = messages.reply(store, client_id, str(body.get("text") or ""), who, str(body.get("translation") or "") or None)
        result = messages.notify(store, Notifier(self.portal_root / "outbox.jsonl", cases_root=self.data_root, store=store), client_id, self._sign_in_link(store, client_id), sent["id"])
        return {"message": sent["id"], "delivery": result}

    def message_done(self, client_id: str, body: dict) -> dict:
        """Marks a client's message handled without an answer in the portal (the office phoned them, say)."""
        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: the change records who made it.")
        done = self._portal_client(client_id).handle_messages(client_id, who, message_id=str(body.get("id") or "") or None)
        return {"handled": len(done)}

    def request_done(self, client_id: str, body: dict) -> dict:
        """The office has read the client's answer to a question (or sent document): the portal's "Sent to the office" line for it goes."""
        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: the change records who made it.")
        done = self._portal_client(client_id).settle_requests(client_id, who, [str(body["id"])] if body.get("id") else None)
        return {"settled": len(done)}

    def _message_events(self, client_id: str) -> list[dict]:
        """The thread as timeline events with the time they happened (the office's clock, src/clock.py): the case page shows when the client wrote."""
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "messages.json").exists():
            return []
        out = []
        for m in json.loads((self.portal_root / "clients" / client_id / "messages.json").read_text(encoding="utf-8")):
            when = clock.local(m["at"])
            out.append({"date": when.date().isoformat(), "kind": "message", "doc": None,
                        "what": ("Message from the client" if m["from"] == "client" else f"Answer to the client from {m.get('by') or 'the office'}")
                        + f" at {when.strftime('%I:%M %p').lstrip('0')}"})
        return out

    def set_filed(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """Records the mailing of a packet (src/prefile.py): which filing, the date, carrier and tracking
        number -- refused while a pre-mailing check fails, unless the attorney gives the reason."""
        import prefile

        who = str(body.get("reviewer") or "").strip()
        d = self.client_dir(client_id)
        with self._lock:
            if body.get("filed", True) is False:
                undone = prefile.undo_filing(d, who, role)
                self._requery(d)
                return undone | {"client_page": self._push_client_page(client_id)}
            record = prefile.record_filing(d, str(body.get("filing") or "i485"), str(body.get("mailed_on") or ""), str(body.get("carrier") or ""),
                                           str(body.get("tracking") or ""), who, body.get("override"), role)
            self._requery(d)
            page = self._push_client_page(client_id)
            import journey

            if page in ("now", "tonight") and not journey.client_shows(record):  # the client's page says nothing of this kind of filing: never "shows it now"
                page = "not_shown"
        self._label("record_confirmed", d, "filed", who)
        return record | {"client_page": page}

    def _push_client_page(self, client_id: str) -> str:
        """The client's page written from the case now, the way the overnight run does it (journey.push_client), so the mailing shows on the
        phone at once: "now", "none" (this client is not in the portal) or "tonight" (it could not be written here; the overnight run will)."""
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            return "none"
        import journey

        try:
            journey.push_client(self.data_root, self.portal_root, client_id, strict=True)
            return "now"
        except Exception as exc:  # noqa: BLE001 -- the mailing is recorded; the overnight run writes the page
            sys.stderr.write(f"client page not written: {type(exc).__name__}\n")
            return "tonight"

    def prefile(self, client_id: str, filing: str | None) -> dict:
        import prefile

        d = self.client_dir(client_id)
        return prefile.check(d, filing or "i485") | {"filings": prefile.filings(d), "carriers": prefile.CARRIERS}

    def _approvals_gather(self, user: dict | None, *, with_progress: bool = False):
        """What the attorney's queue holds for this reader: the cases' items (from the roster: no case folder is read) and the firm's own (src/approvals.py)."""
        import approvals

        who = user if self.accounts is not None else None
        if with_progress:
            cases, pending = self.roster.approvals(who, with_progress=True)
            firm = approvals.firm_items(approvals.FirmContext(self.data_root, self.roster.told(who)))
            return cases, firm, pending if self.scaled else 0
        return self.roster.approvals(who), approvals.firm_items(approvals.FirmContext(self.data_root, self.roster.told(who)))

    def approvals_queue(self, q: dict, user: dict | None) -> dict:
        """My approvals: every open item that needs an attorney, across the cases this attorney may be told of, grouped by kind, the oldest first, 50 a page
        (src/approvals.py). Counts and rows alike leave out a restricted case the attorney is not named on."""
        import approvals

        self._attorneys_only(user, "The approvals queue")
        cases, firm, pending = self._approvals_gather(user, with_progress=True)
        return approvals.queue(cases, firm, q) | {"staff": [p for p in self._people_public() if p["role"] == "paralegal"],
                                                  "still_reading": pending}  # captured with the case items, before any later background publication

    def today(self, q: dict, user: dict | None) -> dict:
        """Today: the cases this person may open, the ready ones first, each with its next deadline, the steps to a signed packet, who holds them and the three to do first
        (src/day_plan.py), 50 a page. Built from the roster's entries: no case folder is read. A restricted case is in no row and no count for anyone not named on it."""
        import day_plan

        who = user if self.accounts is not None else None
        plans, pending = self.roster.day_plans(who, with_progress=True)
        out = day_plan.listing(plans, (user or {}).get("role"), q)
        out["still_reading"] = pending if self.scaled else 0  # this plan snapshot cannot be paired with a later completion count
        return out

    def today_line(self, user: dict | None) -> dict:
        """"N cases can reach a signed packet today" for My work, with the same count Today gives."""
        import day_plan

        who = user if self.accounts is not None else None
        n = sum(1 for _c, _n, day, _r in self.roster.day_plans(who) if day_plan.can_reach(day))
        return {"can_reach": n, "line": day_plan.line(n)}

    def approvals_line(self, user: dict | None) -> dict | None:
        """"N approvals waiting, the oldest from MM/DD/YYYY" for an attorney's My work (None for anyone else: no one else is told there is a queue)."""
        import approvals

        if self.accounts is not None and (user or {}).get("role") != "attorney":
            return None
        cases, firm = self._approvals_gather(user)
        s = approvals.summary(cases, firm)
        return s | {"line": approvals.line(s)}

    def work(self, owner: str | None = None, role: str | None = None, user: dict | None = None, limit: int | str | None = None) -> dict:
        """Every case's open steps and deadlines, for one person's list (src/journey.py via the dashboard cache): the cases they may see. Each list is cut to its
        first `limit` lines (50; the screen asks for more); "totals" says how many there are in all, and a task for the whole firm is never cut."""
        from review.overview import my_work

        try:
            limit = max(10, min(int(limit or 50), 2000))
        except (TypeError, ValueError):
            raise ValueError("The limit must be a number.") from None
        self._sweep()  # (as on the Documents tab)
        who = self._who(role, user)
        out = my_work(self._open_rows(self._unrestricted(self._rows(who), who)) + self.prospect_tasks(user), owner,
                      person=(user or {}).get("email") if owner == (user or {}).get("role") else None)  # + the open prospects' tasks (src/prospects.py)
        firm = [x for x in out["steps"] if str(x["id"]).startswith("visa_bulletin.")]  # one task for the firm, shown once for every client it holds up
        out["totals"] = {k: len(out[k]) for k in ("deadlines", "review", "steps", "messages", "retakes", "new_photos", "answers_week")}
        out |= {k: out[k][:limit] for k in ("deadlines", "review", "messages", "retakes", "new_photos", "answers_week")} | {"steps": firm + [x for x in out["steps"] if x not in firm][:limit], "limit": limit}
        out["today"] = self.today_line(user)  # one line to Today (src/day_plan.py)
        out["jobs"] = jobs.counts(self.jobs_root)  # readings waiting or running now (src/jobs.py)
        if owner in (None, "attorney"):  # the prospects waiting for the attorney's decision: the attorney's list, not the paralegal's
            out["approvals"] = self.approvals_line(user)  # one line to the queue of everything an attorney alone approves (src/approvals.py)
            import prospects

            out["prospects"] = prospects.waiting_rows(self.data_root, self.portal_root, self._prospect_gate(user)) if prospects.folder(self.data_root).is_dir() else []
        try:  # the notice inbox: notices waiting for a person, scans not read yet (src/inbox.py)
            import inbox

            out["inbox"] = inbox.counts(self.data_root, inbox.default_path(self.data_root))
        except Exception as exc:  # noqa: BLE001 -- My work shows without it
            sys.stderr.write(f"inbox not counted: {type(exc).__name__}\n")
        return out

    # -- the notice inbox (src/inbox.py): the day's USCIS and court mail, routed to each case --------------

    def _inbox_paths(self) -> dict:
        import inbox
        import index

        return {"inbox": inbox.default_path(self.data_root), "db_path": index.default_path(self.data_root),
                "state_path": self.data_root.parent / "batch_state.json"}  # the overnight run's record of each folder (src/overnight.py)

    def inbox_view(self, user: dict | None = None) -> dict:
        """The notice inbox as this person may see it: a notice for a case they may not open (or a VAWA, T, U or asylum notice
        nobody has matched yet) waits nameless, "a restricted case's notice: an attorney places it"; what was routed to such a
        case is not in the recent list (src/restricted.py)."""
        import inbox

        out = inbox.view(self.data_root, self._inbox_paths()["inbox"])
        who = user if self.accounts is not None else None
        if who is None or who.get("role") == "attorney":
            return out
        # Secondary history is independent of roster completeness.
        ids = {e.get("case") for e in out["recent"] if isinstance(e.get("case"), str)}
        for w in out["waiting"]:
            candidates = w.get("candidates") or []
            if isinstance(candidates, (list, tuple)):
                ids.update(c.get("case") for c in candidates if isinstance(c, dict) and isinstance(c.get("case"), str))
        scope, allowed = self._current_list_scope(who, ids)
        out["recent"] = [e for e in out["recent"] if isinstance(e.get("case"), str) and allowed(e["case"])]
        out["waiting"] = [{k: w.get(k) for k in ("id", "scanned", "pages", "of", "queued_at")}
                          | {"restricted": True, "status": "restricted", "what": restricted.NOTICE, "why": "", "candidates": [], "names": [], "a_number": None}
                          if self._notice_closed(w, scope) else w for w in out["waiting"]]
        return out

    @staticmethod
    def _notice_closed(entry: dict, scope: dict) -> bool:
        """A waiting notice this person may not see or place: one of its possible cases is hidden from them, or it is a notice
        of a protected filing (restricted.PROTECTED_FORMS) with no case of theirs among its candidates."""
        candidates = entry.get("candidates") or []
        if not isinstance(candidates, (list, tuple)) or any(not isinstance(c, dict) or not isinstance(c.get("case"), str)
                or not c["case"] or len(c["case"]) > 200 or c["case"] in (".", "..") or any(ch in c["case"] for ch in ("/", "\\", "\0")) for c in candidates):
            return True
        cases = {c["case"] for c in candidates}
        if cases & scope["hidden"]:
            return True
        return restricted.protected_form(entry.get("form")) and not (scope["confidential"] is None or cases & scope["confidential"])

    def _notice_for(self, entry_id: str, user: dict | None) -> None:
        """Refuses a waiting notice this person may not see (its scan, placing it, setting it aside): an attorney places it."""
        import inbox

        who = user if self.accounts is not None else None
        if who is None or who.get("role") == "attorney":
            return
        entry = next((q for q in inbox._queue(self._inbox_paths()["inbox"]) if q["id"] == entry_id), None)
        if entry is not None and self._notice_closed({"candidates": entry.get("candidates"), "form": (entry.get("read") or {}).get("form")}, self._scope(who)):
            raise PermissionError(restricted.NOTICE)

    def inbox_read(self, role: str | None = None, user: dict | None = None) -> dict:
        """"Read the inbox now" on My work: what the overnight run does, written down as a job for the worker (src/jobs.py); the screen answers at once and shows how far
        it is. A reading already waiting or running is the one that answers."""
        if self.accounts is not None and role not in ("paralegal", "attorney"):
            raise PermissionError("Only the firm's staff can read the inbox.")
        name = (user or {}).get("name") or ""
        job = next((j for j in jobs.jobs(self.jobs_root, kind="inbox_read", recent=0) if j["state"] in ("queued", "running")), None)
        already = job is not None and (job.get("by") or "") != name  # another person pressed it first: theirs is the reading, and its answer is theirs
        job = job or jobs.submit(self.jobs_root, "inbox_read", None, by=name)
        return self._job_started(job) | self.inbox_view(user) | ({"already": "Already being read: someone else started it a moment ago. It shows here when it is done."} if already else {})

    def health(self, user: dict | None = None) -> dict:
        self._attorneys_only(user, "The health of the install")
        from review.health import summary

        return summary(self)

    def posture(self, user: dict | None = None) -> dict:
        """Settings, "This computer": what the machine's own duties read (src/posture.py: the disk encrypted, the screen locked, a recent backup on another device, the system
        updated, the firewall on), as the review app and the overnight run last kept them. The attorney's: the data statement is hers. Read only: nothing here changes the computer."""
        import posture

        self._attorneys_only(user, "This computer")
        return posture.view(posture.read(posture.path_for(self.firm_data)))

    def start_posture(self) -> threading.Thread | None:
        """At the review app's start: the computer is read, in the background (a command may take seconds), and the result kept (src/posture.py). Off with I485_POSTURE_CHECKS=0."""
        import posture

        if not posture.enabled():
            return None

        def read() -> None:
            try:
                posture.refresh(self.firm_data)
            except Exception as exc:  # noqa: BLE001 -- the screen says "not checked yet"; the night tries again
                sys.stderr.write(f"the computer's posture was not read ({type(exc).__name__})\n")

        t = threading.Thread(target=read, name="posture", daemon=True)
        t.start()
        return t

    def restore_drill_run(self, user: dict | None = None) -> dict:
        """"Run it now" under Keeping current: the restore drill (src/backups.py drill) as a job for the worker, never on the request; the screen answers at once and watches the job.
        An attorney's: the drill opens the whole backup. One already waiting or running is the one that answers."""
        self._attorneys_only(user, "The restore drill")
        job = next((j for j in jobs.jobs(self.jobs_root, kind="restore_drill", recent=0) if j["state"] in ("queued", "running")), None)
        return self._job_started(job or jobs.submit(self.jobs_root, "restore_drill", None, by=(user or {}).get("name") or ""))

    def inbox_cases(self, q: dict, user: dict | None = None) -> list:
        """The "this belongs to" picker: cases by name, folder, A-Number or receipt number; only the cases this person may see."""
        import inbox

        found = inbox.Directory(self.data_root, self._inbox_paths()["db_path"], refresh=False).find(q.get("q") or "")
        return [c for c in found if self.visible(user, c["case"])]

    def inbox_place(self, body: dict, user: dict | None = None) -> dict:
        """A reviewer's choice: the waiting notice belongs to this case. Checked here (who, the notice, a case this person may see), then written down as a job: the
        worker reads the notice into the case (src/jobs.py) and the screen shows it going."""
        import inbox

        p = self._inbox_paths()
        who = str(body.get("reviewer") or "").strip()
        self._notice_for(str(body.get("id") or ""), user)
        case = str(body.get("case") or "")
        if not self.visible(user, case):
            case = ""  # a case hidden from this person is placed like one that isn't there: the same answer for both
        if not who:
            raise ValueError("Enter your name first: every change records who made it.")
        inbox._take(p["inbox"], str(body.get("id") or ""))  # LookupError: not waiting any more
        if not (self.data_root / case / "fact_graph.json").exists() or not _plain_name(case):
            raise ValueError("Choose the case from the list.")
        job = jobs.submit(self.jobs_root, "inbox_place", None, by=who, args={"id": str(body.get("id") or ""), "case": case, "who": who})
        return self._job_started(job) | self.inbox_view(user)

    def inbox_not_ours(self, body: dict, user: dict | None = None) -> dict:
        import inbox

        self._notice_for(str(body.get("id") or ""), user)
        inbox.not_ours(self._inbox_paths()["inbox"], str(body.get("id") or ""), str(body.get("note") or ""), str(body.get("reviewer") or "").strip())
        return self.inbox_view(user)

    def inbox_file(self, entry_id: str, user: dict | None = None) -> bytes:
        """A waiting notice's scan, to look at before placing it: not one this person may not see."""
        import inbox

        self._notice_for(entry_id, user)
        return inbox.waiting_file(self._inbox_paths()["inbox"], entry_id).read_bytes()

    def _unrestricted(self, rows: list[dict], who: dict | None, *, current_confidential=None) -> list[dict]:
        """The rows as this person may see them: an expiring document that is confidential (8 U.S.C. 1367, 8 CFR 208.6: a protected
        case's, or a confidential type's) is for an attorney and the staff named on its case only, as on the Search page, so other
        people's rows leave those deadlines out (review/expiring.py, src/restricted.py)."""
        if who is None or who.get("role") == "attorney":
            return rows
        from review.expiring import restricted as confidential

        sees = (lambda r: r in current_confidential) if current_confidential is not None else ((lambda r: self.roster.sees_confidential(who, r)) if self.scaled else (lambda r: restricted.sees_confidential(who, self.data_root / r)))
        return [row | {"journey": (row["journey"] | {"deadlines": [d for d in row["journey"].get("deadlines") or [] if not confidential(d)]})}
                if row.get("journey") and not sees(row["id"]) else row
                for row in rows]

    def reports(self, role: str | None, user: dict | None = None) -> dict:
        """Counts and lists by stage, filing, office and reviewer, documents by type and quality, and last night's run (review/reports.py):
        of the cases this person may see."""
        from review.reports import build

        who = self._who(role, user)
        self._sync_feedback()  # the clients' answers about a step, from the portal's folder onto their cases
        scope, allowed = self._current_list_scope(who)
        rows = [row for row in self._unrestricted(self._rows(who), who, current_confidential=scope["confidential"]) if allowed(row["id"])]
        return build(rows, self.data_root, role=role, scope=scope)

    def _current_list_scope(self, who: dict | None, ids=None):
        """One request's current disk policy, never a retained positive cache."""
        names = frozenset(os.listdir(self.data_root))
        folder = Path(self.portal_root) / "clients" if self.portal_root is not None else None
        portal_names = frozenset(os.listdir(folder)) if folder is not None and folder.is_dir() else frozenset()
        scope = restricted.scope(who, self.data_root, case_ids=ids, inventory=names, with_visibility=True, workers=8)
        visibility = scope.pop("visibility")
        scope["permitted"] = {case for case, visible in visibility.items() if visible}
        def allowed(case):
            if not isinstance(case, str) or not case or len(case) > 200 or case in (".", "..") or any(ch in case for ch in ("/", "\\", "\0")):
                return False
            if case not in visibility:
                visibility[case] = self.may_open(who, case, inventory=(names, portal_names))
                if not visibility[case]:
                    scope["hidden"].add(case)
                else:
                    scope["permitted"].add(case)
            return visibility[case]
        scope["may_open"] = allowed
        for case in ids if ids is not None else names | portal_names:
            allowed(case)
        return scope, allowed

    def _sync_feedback(self) -> None:
        """client_case.sync_all_feedback looks at every client's folder in the portal (a file each: 3 seconds at 2,000 clients on a Windows disk), so as installed it runs in the
        background, at most once in I485_FEEDBACK_EVERY seconds (60), and Reports shows what the last run brought; with 0 it runs first, in the request (the tests, an app with few clients)."""
        import client_case

        every = float(os.environ.get("I485_FEEDBACK_EVERY", "60"))
        if every <= 0 or not self.scaled:
            client_case.sync_all_feedback(self.data_root, self.portal_root, self.roster.touch)
            return
        with self._lock_feedback:
            if time.monotonic() - self._feedback_at < every or (self._feedback_thread is not None and self._feedback_thread.is_alive()):
                return
            self._feedback_at = time.monotonic()
            self._feedback_thread = threading.Thread(target=lambda: self._feedback_once(client_case), name="feedback", daemon=True)
            self._feedback_thread.start()

    def _feedback_once(self, client_case) -> None:
        try:
            client_case.sync_all_feedback(self.data_root, self.portal_root, self.roster.touch)
        except Exception as exc:  # noqa: BLE001 -- Reports shows what it has; the next look tries again
            sys.stderr.write(f"feedback not brought onto the cases ({type(exc).__name__})\n")

    def report_csv(self, table: str, role: str | None, user: dict | None = None) -> bytes:
        from review.reports import to_csv

        found = next((t for t in self.reports(role, user)["tables"] if t["id"] == table), None)
        if found is None:
            raise LookupError("There is no such report.")
        return to_csv(found).encode("utf-8")

    def expiring(self, q: dict, role: str | None, user: dict | None = None) -> dict:
        """What's due, "Expiring documents": every case's expiring documents (src/expiry.py), by office and by reviewer, of the
        cases this person may see; a confidential document's only for those who may see it (counted for the others)."""
        from review.expiring import DEFAULT_HORIZON, expiring

        try:
            days = int(q.get("days") or DEFAULT_HORIZON)
        except ValueError:
            raise ValueError("Choose how many days ahead to look.") from None
        who = self._who(role, user)
        return expiring(self._open_rows(self._rows(who)), self.data_root, horizon_days=max(1, min(days, 3650)), office=q.get("office") or None,
                        reviewer=q.get("reviewer") or None, role=role,
                        confidential_ok=None if who is None else (lambda case: self.roster.sees_confidential(who, case) if self.scaled else restricted.sees_confidential(who, self.data_root / case)))

    def search(self, q: dict, role: str | None, user: dict | None = None) -> dict:
        """The Search box and the saved searches (src/index.py): documents across every case this person may see. A restricted
        case they may not open is not searched at all, and not counted (src/restricted.py); a confidential document (8 U.S.C.
        1367, 8 CFR 208.6) in a case they may see is listed for the attorney and the staff named on the case, and the others are
        told how many more there are."""
        import index
        import offices

        scope = self._scope(self._who(role, user))
        access = {"include_confidential": scope["confidential"] is None, "hidden_cases": sorted(scope["hidden"]),
                  "confidential_cases": sorted(scope["confidential"] or ())}
        try:
            index.keep_up(self.data_root)  # cases the overnight run hasn't reached yet, and a missing or damaged index file
        except Exception as exc:  # noqa: BLE001 -- search shows what the index has; the nightly run tries again
            sys.stderr.write(f"search index not refreshed: {type(exc).__name__}\n")
        found = index.search(q.get("q") or "", types=[t for t in (q.get("type") or "").split(",") if t], person=q.get("person") or None,
                             office=q.get("office") or None, expiring_before=q.get("expiring_before") or None,
                             expiring_after=q.get("expiring_after") or None, issued_before=q.get("issued_before") or None,
                             no_filing=q.get("no_filing") or None, limit=q.get("limit") or 50, db_path=index.default_path(self.data_root), **access)
        every = offices.offices()
        import engagement

        for hit in found.get("results") or []:  # each result names its case: the person, the kind of case, the id (buyer visits 3, 4)
            known, kind = self.roster.kind_of(hit["case"]) if self.scaled else (False, None)
            hit["kind"] = kind if known else self._name_and_kind(self.data_root / hit["case"])[1]
            ended = self.roster.ended(hit["case"]) if self.scaled else engagement.end_info(self.data_root / hit["case"])  # an ended case stays findable, and says it ended (src/engagement.py)
            if ended:
                hit["end"] = ended
        found["results"] = self._purge_marks([r | {"id": r["case"]} for r in found.get("results") or []], self._who(role, user))
        import query

        facets = query.facets(self.data_root, **access)  # the pick lists and counts from the query layer (src/query.py); the index when it cannot be read
        return found | {"saved": index.saved_searches(), "offices": [o["name"] for o in every] if len(every) > 1 else [],
                        **(facets if facets is not None else index.facets(index.default_path(self.data_root), **access))}

    def _label(self, fn: str, *args) -> None:
        """Labels for the learning store (src/learning/labels.py) -- learned from
        what people do; a problem there never interrupts the person."""
        try:
            from learning import labels

            getattr(labels, fn)(*args, db_path=self.data_root.parent / "learning.db")
        except Exception:  # noqa: BLE001
            pass

    # -- the ready-to-file packet (src/packet.py) ------------------------------

    def _row(self, client_dir: Path) -> dict:
        from review.overview import review_row

        return review_row(client_dir, self.field_map, self.template, self.catalog)

    def packet_plan(self, client_id: str, filing: str | None = None) -> dict:
        """filing: one of packet.FILINGS; when none is chosen, the one the case's stage leads to (src/journey.py next_filings)."""
        import journey
        import packet

        d = self.client_dir(client_id)
        j = journey.journey(d)
        suggested = [f["filing"] for f in j["next_filings"] if f["now"] and f["filing"] in packet.FILINGS]  # due now
        last = next((r["filing"] for r in reversed(j["filings"]) if r.get("filing") in packet.FILINGS), None)  # else the last one filed
        plan = packet.plan(d, self._row(d), packet.load_filing(filing or (suggested[0] if suggested else last or "i485")))
        plan["suggested"] = suggested
        if plan["filing"] == "i360":
            import i360

            plan["i360"] = i360.status(d)
        if plan["filing"] == "family":
            import family

            plan["family"] = family.status(d)
        if plan["filing"] == "n400":
            import naturalization

            plan["n400"] = naturalization.status(d)
        if plan["filing"] == "i589":
            import asylum

            plan["i589"] = asylum.status(d)
        import filing_questions

        if filing_questions.module(plan["filing"]):  # I-90, I-131, N-600, I-751: one panel for all
            plan["panel"] = filing_questions.status(plan["filing"], d)
        import drafting

        if plan["filing"] in drafting.FILINGS:  # the client's declaration, from their own answers (src/drafting.py)
            plan["declaration"] = drafting.card(d, plan["filing"], self._portal_language(client_id))
        import online_filing

        # paper or online (by PDF upload in the firm's USCIS online account): allowed for this case, why, the online fee
        plan["online"] = online_filing.status(d, plan["filing"], pays=plan["payments"])
        import prefile

        plan["editions"] = prefile.edition_holds(plan["forms"])  # forms USCIS has a newer edition of: this packet waits (src/editions.py)
        from review import bundle

        plan["review_bundle"] = bundle.info(d, plan["filing"])  # the last review bundle built for this packet (src/review/bundle.py)
        import g28

        plan["g28"] = self.g28_card(client_id, plan["filing"]) if g28.governed([f["id"] for f in plan["forms"]]) else None  # the G-28's card (src/g28.py)
        plan["rebuild"] = self.rebuild_card(client_id, plan["filing"])  # "Rebuild the forms" after a release, and the boxes it changed (src/rebuild.py)
        plan["audit"] = self.audit_case(client_id)["rows"]  # the boxes the office changed on this case (src/audit_fill.py)
        return plan

    def _g28_forms(self, d: Path, filing: str | None) -> list[str]:
        """The forms the filing's packet fills for this case (the G-28 card governs the G-28s among them)."""
        import packet

        schema = packet.for_case(packet.load_filing(filing or "i485"), d)
        return [fid for fid, _form in packet.forms_in(schema) if fid not in (schema.get("generated") or {})]

    def g28_card(self, client_id: str, filing: str | None = None) -> dict | None:
        """The review card "The G-28 for this case" (src/g28.py): the address the form will carry, the three Part 4 choices with the form's words,
        the attorney who signs, the client's name, what each other form carries, and whether a person confirmed it. None when the packet has no such G-28."""
        import g28
        from review.state import reviewed_graph

        d = self.client_dir(client_id)
        return g28.card(d, self._g28_forms(d, filing), reviewed_graph(d, g28_card=False))

    def g28_change(self, client_id: str, body: dict, user: dict | None = None) -> dict | None:
        """Confirms the card, changes one choice for this case with a reason, or takes the last change back: under the person's name, a ledger row each."""
        import g28
        from review.state import reviewed_graph

        d = self.client_dir(client_id)
        action, who = str(body.get("action") or ""), str(body.get("reviewer") or "").strip()
        role = (user or {}).get("role") if self.accounts is not None else None
        with self._lock:
            graph = reviewed_graph(d, g28_card=False)
            if action == "confirm":
                g28.confirm(d, who, role, graph)
            elif action == "change":
                g28.change(d, str(body.get("item") or ""), body.get("value"), str(body.get("reason") or ""), who, role, graph)
            elif action == "undo":
                g28.undo(d, who, role)
            else:
                raise ValueError("Choose what to do: confirm the card, change a choice, or take the last change back.")
            self._requery(d)
        return self.g28_card(client_id, body.get("filing"))

    def _rebuild_schema(self, d: Path, filing: str | None) -> dict:
        import packet

        return packet.for_case(packet.load_filing(filing or "i485"), d)

    def rebuild_card(self, client_id: str, filing: str | None = None) -> dict | None:
        """The card "The forms and this release" (src/rebuild.py): the release the packet was built with and this one, whether to offer "Rebuild the forms", and the boxes
        a rebuild changed, old and new with the reason, waiting for a person to confirm them. None when no packet is built."""
        import packet
        import rebuild
        from review.state import is_private_number

        d = self.client_dir(client_id)
        schema = self._rebuild_schema(d, filing)
        return rebuild.card(d, schema.get("filing", "i485"), packet._read(d / schema.get("manifest", "packet.json"), None), is_private_number)

    def rebuild_change(self, client_id: str, body: dict, user: dict | None = None) -> dict | None:
        """Rebuilds the forms with this release (the rules applied to the case again, the forms and the packet filled again), or confirms the boxes it changed: under
        the person's name, a ledger row each. The packet is a draft until confirmed."""
        import audit_fill
        import packet
        import rebuild
        from review.state import is_private_number

        d = self.client_dir(client_id)
        filing, action, who = body.get("filing"), str(body.get("action") or ""), str(body.get("reviewer") or "").strip()
        schema = self._rebuild_schema(d, filing)
        role = (user or {}).get("role") if self.accounts is not None else None

        def build() -> None:
            refill(d, self.field_map, self.template)  # the forms carry every decision made so far
            packet.build(d, self._row(d), who, packet.load_filing(filing or "i485"))

        with self._lock:
            if action == "start":
                card = rebuild.start(d, schema.get("filing", "i485"), schema, who, role, build, audit_fill.Boxes(self.catalog), is_private_number)
            elif action == "confirm":
                card = rebuild.confirm(d, schema.get("filing", "i485"), schema, who, role, build, is_private_number)
            elif action == "keep":
                card = rebuild.decline(d, schema.get("filing", "i485"), schema, who, role, is_private_number)
            else:
                raise ValueError("Choose what to do: rebuild the forms, confirm the boxes that changed, or keep the previous forms.")
            self._requery(d)
        return card

    def audit_case(self, client_id: str) -> dict:
        """The boxes the office changed on this case, with the product's value and the office's: the case's own page only (src/audit_fill.py). The firm-wide view
        (Reports) never carries a value."""
        import audit_fill
        from review.state import is_private_number, mask_number

        self.client_dir(client_id)
        rows = audit_fill.for_case(audit_fill.read(self.data_root), client_id, lambda c: not restricted.is_restricted(self.data_root / c))  # no case's page tells of a protected one
        for r in rows:
            if is_private_number(r["ident"], r["box"]):  # a number the screen never prints in full, as on every review card
                r["before"], r["after"] = mask_number(r["before"]), mask_number(r["after"])
            r.pop("ident")
        return {"rows": rows}

    def filing_mode(self, client_id: str, body: dict) -> dict:
        """Paper or online for one filing (src/online_filing.py)."""
        import online_filing

        filing = str(body.get("filing") or "i485")
        with self._lock:
            online_filing.choose(self.client_dir(client_id), filing, str(body.get("mode") or ""), str(body.get("reviewer") or "").strip())
        return self.packet_plan(client_id, filing)

    def build_online_bundle(self, client_id: str, body: dict) -> dict:
        """The online-filing bundle (src/online_filing.py): the packet re-filled, then the files to upload and the checklist."""
        import online_filing
        import packet

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: the bundle records who built it.")
        d = self.client_dir(client_id)
        schema = packet.load_filing(body.get("filing"))
        with self._lock:
            refill(d, self.field_map, self.template)  # the forms carry every decision made so far
            manifest = online_filing.build_bundle(d, self._row(d), who, schema)
        if not manifest["draft"]:
            self._label("record_confirmed", d, "final_packet", who)
        return manifest

    def add_receipt(self, client_id: str, body: dict) -> dict:
        """The receipt number of a filing made in the USCIS online account, once the case card shows it (src/prefile.py)."""
        import prefile

        with self._lock:
            return prefile.add_receipt(self.client_dir(client_id), str(body.get("receipt") or ""), str(body.get("reviewer") or "").strip(),
                                       body.get("filing") or None)

    def family_answer(self, client_id: str, body: dict, role: str | None = None) -> dict:
        import family

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: every answer records who gave it.")
        with self._lock:
            done = family.answer(self.client_dir(client_id), body.get("values") or {}, who, role)
            self._requery(self.client_dir(client_id))
            return done

    def documents(self, client_id: str) -> dict:
        """The case's documents (src/documents.py): each record without its text, and the taxonomy's words for the screen. A document the
        reader found (or a reviewer called) hard to read says what the client side did about it: the retake asked, the office's own scan
        (never put back on the client), or the newer photo the client sent (then its quality reads "not known" until the reader runs)."""
        import documents

        self._sweep()  # a reading a stopped worker left is "Did not finish: will be read tonight", never "being read now"
        d = self.client_dir(client_id)
        data = documents.with_case_confidentiality(d, documents.load(d, measure=True))  # a protected case protects every document in it
        used = {r["type"] for r in data["documents"]}
        import translation

        foreign = translation.summaries(d)  # a foreign-language document: what the extractors found in it, in a paragraph
        notes = self._photo_notes(client_id, data["documents"])  # the client's side: a retake asked, a new photo in (portal/engine)
        result = {"documents": [{k: v for k, v in r.items() if k not in ("text", "translated")} | {"name": documents.name(r["type"]), "short": documents.short_name(r["type"])}
                              | ({"summary": foreign[(r.get("doc_ids") or r.get("files") or [""])[0]]["summary"],
                                  "translation": translation.entry(d, r["id"], record=r)["state"] if translation.needs(r) else None}
                                 if (r.get("doc_ids") or r.get("files") or [""])[0] in foreign else {})
                              | notes.get(r["id"], {})
                              for r in data["documents"]],
                "boundaries": __import__('document_instances').views(d),
                "subject_reviews": [row | {"facts": [fact | {"label": self.catalog.label(fact["key"])} for fact in row["facts"]]}
                                    for row in __import__('subject_attribution').views(d)],
                "case_subjects": data.get("case_subjects", {}).get("people", []),
                "built": data.get("built"), "roles": documents.roles(), "qualities": list(documents.QUALITIES), "languages": list(documents.LANGUAGES), "arrived": self._arrived(client_id),
                "names": {t: documents.name(t) for t in used}, "short_names": {t: documents.short_name(t) for t in used},
                "undated": sorted(t for t in used if documents.type_info(t).get("has_dates") is False),  # "none on it": this kind carries no date
                "dated": sorted(t for t, spec in documents.types().items() if spec.get("expires")) + ["police_clearance"]}  # a reviewer can set these types' dates
        from review.evidence import document_sources
        document_sources(d, result["documents"])
        self._capture_notes(client_id, result["documents"])
        import review_automation
        for boundary in result["boundaries"]:
            boundary["shadow"] = review_automation.boundary_proposal(boundary)
        for row in result["subject_reviews"]:
            row["shadow"] = review_automation.subject_proposal(row, result["case_subjects"], reviewed_sources=result["subject_reviews"])
        return result

    def _capture_notes(self, client_id, rows):
        """Link this client's exact retained upload originals, never filenames across cases."""
        if self.portal_root is None:
            return
        from portal.store import PortalStore
        from review.evidence import safe
        folder = self.portal_root / "clients" / client_id
        meta = self.client_dir(client_id) / "meta.json"
        if not safe(meta, self.client_dir(client_id)):
            return
        try:
            source = Path(json.loads(meta.read_text(encoding="utf-8"))["source_folder"])
            if source.resolve(strict=True) != (folder / "uploads").resolve(strict=True):
                return
            uploads = PortalStore(self.portal_root).uploads(client_id)
        except (OSError, ValueError, KeyError):
            return
        for row in rows:
            originals = {name.split("#", 1)[0] for name in row.get("files", []) + row.get("doc_ids", [])}
            current_hashes = {part["doc"].split("#", 1)[0]: part["source_location"].get("source_sha256")
                              for part in row.get("source_locations", []) if part.get("source_location", {}).get("state") == "current"}
            capture = [upload for upload in uploads if upload.get("stored") in originals and isinstance(upload.get("capture"), dict)
                       and upload.get("sha256") and current_hashes.get(upload["stored"]) == upload["sha256"]]
            row["capture_artifacts"] = [{"upload_id": u["id"], **u["capture"]} for u in capture]

    def capture_image(self, client_id, upload_id, kind):
        """Hash-check a linked immutable original/derivative under current case authority."""
        from portal.store import PortalStore
        from review.evidence import safe, Unavailable
        import hashlib
        if self.portal_root is None or kind not in {"original", "derivative"}:
            raise Unavailable("Capture artifact unavailable.")
        store = PortalStore(self.portal_root)
        matches = [row for row in store.uploads(client_id) if row.get("id") == upload_id and isinstance(row.get("capture"), dict)]
        if len(matches) != 1:
            raise Unavailable("Capture artifact unavailable.")
        capture = matches[0]["capture"]
        name, expected = capture.get(kind + "_stored"), capture.get(kind + "_sha256")
        if not isinstance(name, str) or not re.fullmatch(r"capture-evidence/[A-Za-z0-9_-]+\.image", name):
            raise Unavailable("Capture artifact unavailable.")
        folder = store.client_dir(client_id)
        path = folder / name
        if not safe(path, folder) or path.stat().st_size > 20 * 1024 * 1024:
            raise Unavailable("Capture artifact unavailable.")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise Unavailable("Capture artifact changed; inspect the retained original before review.")
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            content_type = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(image.format)
        if content_type is None:
            raise Unavailable("Capture artifact unavailable.")
        return data, content_type

    def document_text(self, client_id: str, record_id: str) -> dict:
        """One stored reading on explicit request, with current source status."""
        import documents
        from review.evidence import Unavailable, document_sources
        if not isinstance(record_id, str) or not record_id or len(record_id) > 128 or any(ord(c) < 32 for c in record_id):
            raise LookupError("Unknown document record.")
        d = self.client_dir(client_id)
        data = documents.read(d)
        if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
            raise Unavailable("The document reading is unavailable. Read the original again.")
        matches = [row for row in data["documents"] if isinstance(row, dict) and row.get("id") == record_id]
        if len(matches) != 1:
            raise LookupError("Unknown document record.")
        row = dict(matches[0])
        if any(row.get(field) is not None and not isinstance(row[field], str) for field in ("text", "translated")):
            raise Unavailable("The document reading is unavailable. Read the original again.")
        document_sources(d, [row])
        return {"id": record_id, "text": row.get("text") or "", "translated": row.get("translated") or "",
                "record_built": data.get("built"), "source_locations": row["source_locations"],
                "reading_note": "Stored extracted text from the recorded reading; compare it with the original. This is not a field approval.",
                "translation_note": "Stored English translation; this text alone is not a certified translation."}

    def _arrived(self, client_id: str) -> list[dict]:
        """What the client sent that nobody has read yet (portal/engine.arrived), each with its date as the office writes it: the
        Documents tab's "New from the client" rows."""
        from portal.engine import arrived
        from portal.store import PortalStore
        from review.state import us_date

        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            return []
        return [a | {"on": us_date(a.get("at"))} for a in arrived(PortalStore(self.portal_root).uploads(client_id))]

    def _photo_notes(self, client_id: str, records: list[dict]) -> dict[str, dict]:
        """{record id: {"client_note", and for a replaced photo "quality": "unknown"}} from the client's side (portal/engine.photo_status)."""
        from portal.engine import photo_status
        from portal.store import PortalStore
        from review.state import us_date

        in_portal = self.portal_root is not None and (self.portal_root / "clients" / client_id / "profile.json").exists()
        store = PortalStore(self.portal_root) if in_portal else None
        status = photo_status(records, store.uploads(client_id) if store else [], store.tasks(client_id) if store else [], in_portal)
        out = {}
        for record_id, s in status.items():
            photo = s.get("new_photo")
            if photo:
                out[record_id] = {"quality": "unknown", "quality_set_by": None,
                                  "client_note": f"New photo received {us_date(photo.get('at'))}" + (" (not read yet)" if photo["waiting"] else ", replaces this one")}
            elif s["retake"] == "asked":
                out[record_id] = {"client_note": "Retake asked of the client" + (f" ({us_date(s['asked_at'])})" if s.get("asked_at") else "")}
            elif s["retake"] == "office_scan":
                out[record_id] = {"client_note": "This is the office's scan: the client is not asked"}
            elif s["retake"] == "no_portal":
                out[record_id] = {"client_note": "This client is not in the portal, so nobody is asked for another photo"}
            else:  # marked, and the client's list does not have it yet
                out[record_id] = {"client_note": "Retake not asked yet"}
        return out

    def document_change(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """A reviewer sets whose a document is, its language, how readable it is, or what else it shows (a role tag). A photo called blurry, cut off or
        partial is asked of the client at once (the portal's retake task), unless the office scanned it itself: then the answer says
        so. "note" is what happened, in words for the screen."""
        import documents

        who = str(body.get("reviewer") or "").strip()
        doc_id, field, value = str(body.get("id") or ""), str(body.get("field") or ""), str(body.get("value") or "")
        if field in ("boundaries", "boundary_undo"):
            import document_instances
            with self._lock:
                document_instances.resolve(self.client_dir(client_id), doc_id, value, str(body.get("fingerprint") or ""), who, role,
                                           undo=field == "boundary_undo")
                self._requery(self.client_dir(client_id))
            return self.documents(client_id) | {"note": "Document boundaries reread; affected field approvals must be reviewed again"}
        if field in ("subject_person", "subject_name", "subject_assignment", "subject_undo"):
            import subject_attribution
            d = self.client_dir(client_id)
            with self._lock:
                if field == "subject_person":
                    subject_attribution.add_person(d, value, str(body.get("case_role") or ""), who, role)
                elif field == "subject_name":
                    person = subject_attribution.rename_person(d, doc_id, value, who, role)
                    if person["case_role"] == "applicant" and self.portal_root is not None:
                        from portal.store import PortalStore
                        store = PortalStore(self.portal_root)
                        if client_id in store.clients():
                            profile = store.profile(client_id)
                            if profile.get("name") != person["label"]:
                                history = list(profile.get("name_history") or [])
                                history.append({"name": profile.get("name"), "changed_to": person["label"], "who": who, "role": role,
                                                "at": person["at"], "subject_id": person["id"], "subject_revision": person["revision"]})
                                store.update_profile(client_id, name=person["label"], name_history=history)
                else:
                    subject_attribution.assign(d, doc_id, str(body.get("fingerprint") or ""), body.get("mappings", {}), who, role,
                                               reference_only=body.get("reference_only", False), reference_edges=body.get("reference_edges", []),
                                               note=str(body.get("note") or ""), undo=field == "subject_undo")
                self._requery(d)
                if self.portal_root is not None and (self.portal_root / "clients" / client_id / "profile.json").exists():
                    from portal.engine import sync_confirmations
                    from portal.store import PortalStore
                    sync_confirmations(PortalStore(self.portal_root), client_id, d)
            return self.documents(client_id) | {"note": "Case person added" if field == "subject_person" else
                                                "Fact subjects updated; affected field approvals require a fresh review"}
        change = {"person": documents.set_person, "quality": documents.set_quality, "tag": documents.tag, "untag": documents.untag,
                  "dates": documents.set_dates, "language": documents.set_language}.get(field)
        if change is None:
            raise ValueError("Choose what to change: whose document it is, its language, its quality, its dates, or what it shows.")
        aside = asked = None
        keep = {"yes": True, "no": False}.get(str(body.get("keep_translation") or "").lower())
        with self._lock:
            if field == "language":
                question = documents.translation_question(self.client_dir(client_id), doc_id, value) if keep is None else None
                if question:  # nothing is changed until the person answers: one wrong click would drop the certified-translation requirement
                    asked = {"question": question, "id": doc_id, "field": field, "value": value}
                else:
                    documents.set_language(self.client_dir(client_id), doc_id, value, who, role, keep)
            else:
                change(self.client_dir(client_id), doc_id, value, who, role)
            if field == "person":  # a certificate of translation names the person (src/translation.py)
                import translation

                translation.person_changed(self.client_dir(client_id), doc_id, who)
                # a document set to someone else is not the client's: the Decision log says what was set aside, and the client's questions
                # it caused leave their phone now (or come back when it is set back to the client)
                aside = documents.person_changed(self.client_dir(client_id), doc_id, who, role)
                if self.portal_root is not None and (self.portal_root / "clients" / client_id / "profile.json").exists():
                    from portal.engine import sync_confirmations
                    from portal.store import PortalStore

                    sync_confirmations(PortalStore(self.portal_root), client_id, self.client_dir(client_id))
            if field == "quality" and self.portal_root is not None and (self.portal_root / "clients" / client_id / "profile.json").exists():
                from portal.engine import sync_retakes
                from portal.store import PortalStore

                sync_retakes(PortalStore(self.portal_root), client_id, self.client_dir(client_id))  # the retake task now, not at the next run
            self._requery(self.client_dir(client_id))
        out = self.documents(client_id)
        row = next((r for r in out["documents"] if r["id"] == doc_id), {})
        return out | ({"note": row["client_note"]} if field == "quality" and row.get("client_note") else {}) | ({"note": aside} if field == "person" and aside else {}) \
            | ({"ask": asked} if asked else {}) \
            | ({"note": "The document still needs a translation, as you chose" if row.get("translation_kept", {}).get("keep") else
                "The document no longer needs a translation, as you chose: your answer is recorded"}
               if field == "language" and keep is not None and row.get("translation_kept") else {})

    def absence(self, client_id: str, filing: str | None = None) -> dict:
        """"Papers this filing asks about" (src/absence.py): each in the folder, marked as one the client has none of (who, when, why), or neither,
        with the client's own word beside it. filing: one of packet.FILINGS; none chosen: the one the case's stage leads to, as the packet tab does."""
        import absence
        import journey
        import packet
        from review.state import reviewed_graph

        d = self.client_dir(client_id)
        if not filing:
            j = journey.journey(d)
            suggested = [f["filing"] for f in j["next_filings"] if f["now"] and f["filing"] in packet.FILINGS]
            filing = suggested[0] if suggested else next((r["filing"] for r in reversed(j["filings"]) if r.get("filing") in packet.FILINGS), None) or "i485"
        graph = reviewed_graph(d) if (d / "fact_graph.json").exists() else None
        out = absence.listing(d, filing, graph)
        choices = [(f, len(absence.asked(packet.load_filing(f)))) for f in packet.FILINGS]
        return out | {"filings": [{"id": f, "title": packet.filing_title(f)} for f, n in choices if n or f == out["filing"]]}

    def absence_change(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """The client has no such document: record it (a paper, a reason from the short list, a free line), or take the mark off. The forms are filled
        again at once, so the boxes read NOT APPLICABLE on the form the paralegal opens next; the client's portal list follows (closed with "The office
        has what it needs", or open again)."""
        import absence

        who, action, paper = str(body.get("reviewer") or "").strip(), str(body.get("action") or ""), str(body.get("paper") or "")
        d = self.client_dir(client_id)
        with self._lock:
            if action == "mark":
                absence.mark(d, paper, str(body.get("reason") or ""), str(body.get("line") or ""), who, role)
            elif action == "undo":
                absence.undo(d, paper, who, role)
            else:
                raise ValueError("Choose what to do: record that the client has no such document, or take the mark off.")
            closed = self._sync_absences(client_id, d, who)
            try:
                refill(d, self.field_map, self.template)  # the boxes now: NOT APPLICABLE, or what the case held before
            except Exception as exc:  # noqa: BLE001 -- a form that cannot be filled yet (a case not read) does not undo the record
                sys.stderr.write(f"forms not filled after a mark on a paper ({type(exc).__name__})\n")
            self._requery(d)
        return self.absence(client_id, str(body.get("filing") or "") or None) | {"closed": closed}

    def _sync_absences(self, client_id: str, client_dir: Path, who: str) -> int:
        """The client's portal list after a mark or its Undo (portal/engine.py sync_absences); how many of the office's requests were closed."""
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            return 0
        from portal.engine import sync_absences
        from portal.store import PortalStore

        return sync_absences(PortalStore(self.portal_root), client_id, client_dir, who)

    def translation_change(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """A foreign-language document's translation (src/translation.py): make it, type or correct it, choose the translator (the
        attorney's call), record that the translator signed, or take the signature back. Returns the packet page's translations."""
        import translation

        who, doc_id, action = str(body.get("reviewer") or "").strip(), str(body.get("id") or ""), str(body.get("action") or "")
        d = self.client_dir(client_id)
        if action == "translator" and role == "paralegal":
            raise PermissionError("The attorney chooses the translator for each document.")
        if action == "make":  # the translator works outside the lock: a page can take a while
            translation.make(d, doc_id, who, bool(body.get("again")), lock=self._lock)
        else:
            with self._lock:
                if action == "text":
                    translation.set_text(d, doc_id, str(body.get("text") or ""), who)
                elif action == "translator":
                    translation.set_translator(d, doc_id, str(body.get("translator") or ""), who)
                elif action == "sign":
                    translation.sign(d, doc_id, who)
                elif action == "reopen":
                    translation.reopen(d, doc_id, who)
                else:
                    raise ValueError("Choose what to do with the translation.")
        self._requery(d)
        return translation.entry(d, doc_id)

    def declaration_change(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """The client's declaration (src/drafting.py): make the English drafts, edit a paragraph or take the edit back, smooth the
        grammar (when switched on), mark it as the client's final (the attorney), record the date the client signed. Returns the card."""
        import drafting

        who, filing, action = str(body.get("reviewer") or "").strip(), str(body.get("filing") or ""), str(body.get("action") or "")
        if filing not in drafting.FILINGS:
            raise ValueError("This filing has no client declaration here.")
        d, key, lang = self.client_dir(client_id), str(body.get("id") or ""), self._portal_language(client_id)
        if action == "english":  # the translator and the model work outside the lock: a paragraph can take a while
            drafting.make_english(d, filing, who, lock=self._lock, client_language=lang)
        elif action == "smooth":
            drafting.smooth(d, filing, who, lock=self._lock, client_language=lang)
        else:
            with self._lock:
                if action == "edit":
                    drafting.edit(d, filing, key, str(body.get("text") or ""), who, role, lang)
                elif action == "revert":
                    drafting.revert(d, filing, key, who, role, lang)
                elif action == "accept":  # a person takes the grammar suggestion: recorded like an edit
                    drafting.accept_suggestion(d, filing, key, who, role, lang)
                elif action == "decline":
                    drafting.decline_suggestion(d, filing, key, who, role, lang)
                elif action == "language":
                    drafting.confirm_language(d, filing, key, str(body.get("language") or ""), who, role, lang)
                elif action == "include":
                    drafting.include(d, filing, key, bool(body.get("keep")), who, role, lang)
                elif action == "final":
                    drafting.mark_final(d, filing, who, role)
                elif action == "take_back":
                    drafting.take_back(d, filing, who, role)
                elif action == "signed":
                    drafting.client_signed(d, filing, str(body.get("date") or ""), who, role)
                else:
                    raise ValueError("Choose what to do with the declaration.")
        return drafting.card(d, filing, lang)

    # -- ask about this case (src/case_questions.py): the local model reads the case's own record, every sentence cited ------------

    def case_questions(self, client_id: str) -> dict:
        """The case page's box: whether anyone can ask, the practice, the latest questions asked on this case."""
        import case_questions

        return case_questions.panel(self.client_dir(client_id))

    def case_question(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """One question, answered from the case's own record (the model works outside the lock: it can take a while)."""
        import case_questions

        return case_questions.ask(self.client_dir(client_id), str(body.get("question") or ""), str(body.get("reviewer") or ""), role)

    def case_summary(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """The summary for the attorney, built the same way (DRAFT; never saved into a filing)."""
        import case_questions

        return case_questions.summary(self.client_dir(client_id), str(body.get("reviewer") or ""), role)

    def _portal_language(self, client_id: str) -> str | None:
        """The language the client reads the portal in (None when the client isn't in the portal)."""
        path = self.portal_root / "clients" / client_id / "profile.json" if self.portal_root is not None else None
        return json.loads(path.read_text(encoding="utf-8")).get("language") if path is not None and path.exists() else None

    def _family_actor(self, user: dict | None) -> dict:
        if self.accounts is None or not isinstance(user, dict) or not isinstance(user.get("email"), str):
            raise PermissionError("Sign in with a current staff account first.")
        current = next((u for u in self.accounts.users() if u.get("email") == user["email"]), None)
        if not current or current.get("active") is not True or current.get("role") not in ("attorney", "paralegal"):
            raise PermissionError("Sign in with a current staff account first.")
        return current

    def _family_authorizer(self, user: dict | None):
        return lambda case: self.may_open(self._family_actor(user), case)

    def family_candidates(self, client_id: str, query: dict, user: dict | None) -> dict:
        import journey
        from review.case_lists import page
        current = self._family_actor(user)
        authorizer = lambda case: self.may_open(current, case)  # one current account snapshot per bounded search
        journey._family_folder(self.data_root.absolute(), client_id, authorizer)
        needle = query.get("q", "")
        if not isinstance(needle, str) or len(needle) > 200:
            raise ValueError("Search uses at most 200 characters.")
        try:
            size, number = min(50, int(query.get("size", 20))), int(query.get("page", 1))
            if size < 1 or number < 1:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("Page and size must be positive whole numbers.") from None
        if not needle.strip():
            return {"clients": [], "total": 0, "page": 1, "size": size, "pages": 0}
        self.roster.sync()
        with self.roster.lock:
            pending = self.roster.dirty | self.roster.later | self.roster.inflight
            snapshot = {cid: row for cid, row in self.roster.entries.items() if cid != client_id and cid not in pending}
        allowed = {}
        for cid, row in snapshot.items():
            try:
                journey._family_folder(self.data_root.absolute(), cid, authorizer)
            except (LookupError, ValueError, OSError):
                continue
            allowed[cid] = row
        # Current ACL filtering precedes cached labels, counts and pagination.
        result = page(allowed, current, [current], {"scope": "all", "q": needle, "page": number, "size": size})
        return {"clients": [{key: row.get(key) for key in ("id", "name", "kind", "restricted")} for row in result["clients"]],
                **{key: result[key] for key in ("total", "page", "size", "pages")}}

    def family_recovery(self, client_id: str, user: dict | None, body: dict | None = None) -> dict:
        import journey
        authorizer = self._family_authorizer(user)
        folder = journey._family_folder(self.data_root.absolute(), client_id, authorizer)
        if body is None:
            return journey.family_recovery_view(folder, may_open=authorizer)
        if body.get("action") != "recover" or not isinstance(body.get("operation"), str) or not re.fullmatch(r"[0-9a-f]{32}", body["operation"]):
            raise ValueError("Choose the exact recorded family operation.")
        journey.family_recover(folder, body["operation"], may_open=authorizer, jobs_root=self.jobs_root)
        self.roster.touch(client_id)
        return {"recovered": True, "notified": False, "access_changed": False} | journey.family_recovery_view(folder, may_open=authorizer)

    def journey(self, client_id: str, user: dict | None = None) -> dict:
        """Where the case stands (src/journey.py), and what the client sees in the portal."""
        import journey

        d = self.client_dir(client_id)
        j = journey.journey(d, may_open=self._family_authorizer(user) if user is not None else None)
        lang = None
        if self.portal_root is not None:
            import client_case

            client_case.sync_feedback(d, self.portal_root)  # what the client said about a step, from the portal's folder onto the case
        if self.portal_root is not None and (self.portal_root / "clients" / client_id / "profile.json").exists():
            lang = json.loads((self.portal_root / "clients" / client_id / "profile.json").read_text(encoding="utf-8")).get("language")
        from portal.bank import language_names

        timeline = sorted(j["timeline"] + self._message_events(client_id), key=lambda e: e["date"] or "9999")  # the client's messages and our answers, with the time
        return j | {"timeline": timeline, "client_view": journey.client_view(j, lang or "en"), "client_language": lang, "client_language_name": language_names().get(lang),
                    "staff": self._people_public()} | {"deadlines": [self._public_who(x) for x in j["deadlines"]]}  # the choices for the person responsible on a deadline (src/deadlines_set.py): name and role, never an e-mail

    def prepare_sheet(self, client_id: str, appointment_id: str, lang: str | None = None) -> bytes:
        """One appointment's preparation sheet as a PDF, for the office to print (src/client_case.py): in the client's language (or the one asked for), with
        the firm's phone and a DRAFT line while the wording is not approved. A hearing has no sheet here."""
        import client_case
        import journey
        from portal.notify import firm_name

        d = self.client_dir(client_id)
        j = journey.journey(d)
        a = next((x for x in client_case.upcoming(j) if x["id"] == appointment_id), None)
        if a is None:
            raise LookupError("There is no such appointment: it may have passed.")
        if lang not in client_case.LANGS:
            path = self.portal_root / "clients" / client_id / "profile.json" if self.portal_root is not None else None
            lang = (json.loads(path.read_text(encoding="utf-8")).get("language") if path is not None and path.exists() else None) or "en"
        sheet = client_case.sheet(a["kind"], a["form"], j.get("track"), lang, a["bring"], j.get("office_phone"), a["bring_pending"])
        if sheet is None:
            raise ValueError("A court hearing has no preparation sheet: the client's page lists what to bring.")
        pages = journey.settings()["appointment_pages"]["labels"]
        labels = {k: journey._say(pages[k], lang) for k in ("when", "where", "bring")}
        when = client_case.day_words(a["date"], lang, True) + (f" · {client_case.clock_words(a['time'], lang)}" if a.get("time") else "")
        sure = bool(a["where"] and a["where_confirmed"])  # only an address a person checked or typed is printed
        place = {"where": a["where"] if sure else None, "note": journey._say(pages["check_place" if sure else "place_in_letter"], lang)}
        return client_case.sheet_pdf(sheet, {"when": when} | place, labels, firm_name(), draft=bool(j.get("draft")))

    def _public_who(self, d: dict) -> dict:
        """A deadline as the case page gets it: the person responsible by id (person_id), never by e-mail."""
        return d | {"who": self.person_id(d["who"])} if d.get("who") else d

    def path_view(self, client_id: str, user: dict | None) -> dict:
        """The case's Path tab (src/path.py): the template, the approved path and what it derives, a waiting change and what it would derive, each step
        marked done, next or later on the path in use."""
        import journey
        import path

        d = self.client_dir(client_id)
        j = journey.journey(d)
        v = path.view(d, j["template_stages"], user["role"] if user else None)
        at = j["stage_index"]
        for i, x in enumerate(v["current"]["steps"]):
            x["state"] = "done" if i < at else "next" if i == at else "later"
        lang = None
        if self.portal_root is not None and (self.portal_root / "clients" / client_id / "profile.json").exists():
            lang = json.loads((self.portal_root / "clients" / client_id / "profile.json").read_text(encoding="utf-8")).get("language")
        return v | {"stage": j["stage"], "stage_name": j["stage_name"], "track": j["track"], "track_name": j["track_name"], "own_path": j["own_path"],
                    "client_language": lang if lang in path.LANGS else "en"}

    def path_change(self, client_id: str, body: dict, user: dict | None) -> dict:
        """POST /api/path: propose a change (a paralegal's waits; an attorney's is approved with it), approve, refuse, or go back to the template.
        Each is its own ledger row (src/path.py); nothing else is written."""
        import journey
        import path

        d = self.client_dir(client_id)
        who = str(body.get("reviewer") or "").strip() or "the attorney on this computer"
        role = user["role"] if user else None
        action = str(body.get("action") or "")
        with self._lock:
            if action == "propose":
                path.propose(d, body.get("steps"), body.get("removed") or [], str(body.get("reason") or ""), who, (user or {}).get("email"), role,
                             journey.journey(d)["template_stages"])
            elif action == "approve":
                path.approve(d, who, role)
            elif action == "refuse":
                path.refuse(d, who, role, str(body.get("reason") or ""))
            elif action == "undo":
                path.undo(d, who, role, str(body.get("reason") or ""))
            else:
                raise ValueError("A path is proposed, approved, refused or taken back to the template.")
            self._requery(d)
        return self.path_view(client_id, user)

    def journey_mark(self, client_id: str, body: dict, role: str | None = None, user: dict | None = None) -> dict:
        import journey

        who = str(body.get("reviewer") or "").strip()
        action = str(body.get("action") or "")
        d = self.client_dir(client_id)
        if action in ("link", "unlink"):
            current = self._family_actor(user)
            journey.mark(d, action, current["name"], value=body.get("value"), may_open=self._family_authorizer(user), jobs_root=self.jobs_root)
            self.roster.touch(client_id)
            return self.journey(client_id, current)
        if role == "paralegal" and action not in ("bring_confirm", "bring_reject", "place_confirm", "place_reject", "place_set"):  # checking what a notice says is office work, not a legal step
            j = journey.journey(d)
            owner = next((x["owner"] for x in j["steps"] + j["takeover"] if x["id"] == body.get("item")), None)
            if action in ("stage", "track", "ours") or owner == "attorney":
                raise PermissionError("This one is the attorney's to change.")
        with self._lock:
            if action == "person_use":  # a person on the case who is not a client: their details onto the family petition's blank answers
                import family

                if not who:
                    raise ValueError("Enter your name first: every change records who made it.")
                filled = family.use_person(d, str(body.get("item") or ""), who, role)
                return self.journey(client_id, user) | {"filled": filled}
            journey.mark(self.client_dir(client_id), action, who, body.get("item"), body.get("value"), body.get("note"))
            self._requery(self.client_dir(client_id))
            if action in ("bring_confirm", "bring_reject", "place_confirm", "place_reject", "place_set") and self.portal_root is not None:
                try:  # what the client's page and sheet say changes now, quietly (no "there's news")
                    journey.push_client(self.data_root, self.portal_root, client_id, notify=False)
                except Exception as exc:  # noqa: BLE001 -- the overnight run brings the page up to date
                    sys.stderr.write(f"client page not updated ({type(exc).__name__})\n")
        return self.journey(client_id, user)

    def rfe(self, client_id: str, key: str | None = None) -> dict:
        """The client's USCIS requests (RFE / NOID), and -- with key -- one response as it stands."""
        import rfe

        d = self.client_dir(client_id)
        return {"requests": rfe.requests(d)} | ({"response": rfe.plan(d, key)} if key else {})

    def rfe_save(self, client_id: str, body: dict) -> dict:
        import rfe

        with self._lock:
            return rfe.save(self.client_dir(client_id), str(body.get("key") or ""), body.get("items") or [], body.get("send_to") or [],
                            body.get("due") or None, str(body.get("reviewer") or "").strip())

    def rfe_build(self, client_id: str, body: dict) -> dict:
        import rfe

        with self._lock:
            return rfe.build(self.client_dir(client_id), str(body.get("key") or ""), str(body.get("reviewer") or "").strip())

    def filing_answer(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """A filing's own questions (src/filing_questions.py): {filing, values, reviewer}."""
        import filing_questions

        filing = str(body.get("filing") or "")
        if not filing_questions.module(filing):
            raise ValueError(f"No questions for the filing {filing!r}.")
        with self._lock:
            done = filing_questions.answer(filing, self.client_dir(client_id), body.get("values") or {}, str(body.get("reviewer") or "").strip(), role)
            self._requery(self.client_dir(client_id))
            return done

    def i589_answer(self, client_id: str, body: dict, role: str | None = None) -> dict:
        import asylum

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: every answer records who gave it.")
        with self._lock:
            done = asylum.answer(self.client_dir(client_id), body.get("values") or {}, who, role)
            self._requery(self.client_dir(client_id))
            return done

    def n400_answer(self, client_id: str, body: dict, role: str | None = None) -> dict:
        import naturalization

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: every answer records who gave it.")
        with self._lock:
            done = naturalization.answer(self.client_dir(client_id), body.get("values") or {}, who, role)
            self._requery(self.client_dir(client_id))
            return done

    def i360_status(self, client_id: str) -> dict:
        import i360

        return i360.status(self.client_dir(client_id))

    def i360_answer(self, client_id: str, body: dict, role: str | None = None) -> dict:
        import i360

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: every answer records who gave it.")
        with self._lock:
            done = i360.answer(self.client_dir(client_id), body.get("values") or {}, who, role)
            self._requery(self.client_dir(client_id))
            return done

    def build_packet(self, client_id: str, body: dict) -> dict:
        import packet

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: the packet records who built it.")
        d = self.client_dir(client_id)
        schema = packet.load_filing(body.get("filing"))
        with self._lock:
            refill(d, self.field_map, self.template)  # the forms carry every decision made so far
            manifest = packet.build(d, self._row(d), who, schema)
            self._requery(d)
        if not manifest["draft"]:
            self._label("record_confirmed", d, "final_packet", who)
        return manifest

    def packet_choice(self, client_id: str, body: dict) -> dict:
        import packet

        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: the change records who made it.")
        schema = packet.load_filing(body.get("filing"))
        with self._lock:
            packet.choose(self.client_dir(client_id), str(body.get("doc") or ""), body.get("exhibit") or None, who, schema)
            self._requery(self.client_dir(client_id))
        self._label("record_packet_move", self.client_dir(client_id), str(body.get("doc") or ""), body.get("exhibit") or None, who, schema)
        return self.packet_plan(client_id, body.get("filing"))

    def items(self, client_id: str, user: dict | None = None) -> dict:
        self._refill_after_answers(client_id, self.client_dir(client_id))
        import read_scope
        with read_scope.scope():
            return self._items(client_id, user)

    def _items(self, client_id: str, user: dict | None = None) -> dict:
        import engagement
        import g28
        import offices
        import rebuild

        d = self.client_dir(client_id)
        out = build_items(d, self.field_map, self.template, self.catalog)
        __import__("review.evidence", fromlist=["annotate"]).annotate(d, out)
        self._note_foreign(d, out)
        self._note_client_not_asked(client_id, out)
        self._note_client_answered(client_id, out)
        office = offices.for_case(d, (out.get("summary") or {}).get("state"))
        # the office the case is filed from, why, and the others it can move to; who may open the case (src/restricted.py)
        return out | {"office": {"id": office["id"], "name": office["name"], "why": office["why"],
                                 "choices": [{"id": o["id"], "name": o["name"]} for o in offices.offices()]},
                      "access": self.access_state(client_id, user), "end": engagement.end_info(d), "g28_stale": g28.stale_notes(d) + rebuild.notes(d)} | self._portal_note(client_id)

    def _first_call(self, client_id: str, user: dict | None) -> dict | None:
        """The first call this client began with (src/prospects.py first_call), for the case page; nothing for a client who was never a prospect."""
        import prospects

        try:
            return prospects.first_call(self.data_root, self.portal_root, client_id, self._prospect_gate(user)) if prospects.folder(self.data_root).is_dir() else None
        except (LookupError, OSError, ValueError):
            return None

    def _refill_after_answers(self, client_id: str, d: Path) -> None:
        """The cards read a client's typed answer the moment it is in the portal (review/state.py office_answered); the filled I-485
        and the flag report are filled again here when the office's questions changed since they were last filled, so the form the
        paralegal opens carries the answer too."""
        if self.portal_root is None:
            return
        asked, filled = self.portal_root / "clients" / client_id / "requests.json", d / "fact_graph_reviewed.json"
        try:
            if not asked.exists() or (filled.exists() and asked.stat().st_mtime <= filled.stat().st_mtime):
                return
            with self._lock:
                refill(d, self.field_map, self.template)
        except Exception:  # noqa: BLE001 -- the cards still show the answer; the form is filled again at the next decision or packet
            import traceback

            traceback.print_exc()

    # -- who may open a case (src/restricted.py), on the case page ------------------------------

    def access_state(self, client_id: str, user: dict | None = None) -> dict:
        """The case page's "Restricted case" banner and, for an attorney, the controls: restrict it, name the staff who may open
        it, switch automatic messages on. Names in place of emails; the staff to choose from are the active paralegals."""
        d = self.client_dir(client_id)
        people = self.accounts.users() if self.accounts is not None else []
        state = restricted.state(d, {u["email"]: u["name"] for u in people})
        attorney = self.accounts is None or (user or {}).get("role") == "attorney"
        return state | {"can_change": attorney, "accounts": self.accounts is not None,
                        "staff": [{"email": u["email"], "name": u["name"]} for u in people if u["active"] and u["role"] != "attorney"] if attorney else []}

    def access_change(self, client_id: str, body: dict, user: dict | None = None) -> dict:
        """{action: mark | unmark | name | unname | messages_on | messages_off, reason, email}: the attorney's, each kept on the case
        with who, when and why, and in the staff access log."""
        d = self.client_dir(client_id)
        who, role = str(body.get("reviewer") or "").strip(), (user or {}).get("role")
        action, reason, email = str(body.get("action") or ""), str(body.get("reason") or ""), str(body.get("email") or "").strip().lower()
        was = restricted.is_restricted(d)
        with self._lock:
            if action in ("mark", "unmark"):
                restricted.mark(d, action == "mark", reason, who, role)
            elif action in ("name", "unname"):
                if role == "paralegal":
                    raise PermissionError("Only an attorney chooses who may open a restricted case.")
                person = next((u for u in (self.accounts.users() if self.accounts is not None else []) if u["email"] == email), None)
                if action == "name" and (person is None or not person["active"]):
                    raise ValueError("Choose a staff member from the list.")
                if action == "name" and person["role"] == "attorney":
                    raise ValueError(f"{person['name']} is an attorney: attorneys may open every case.")
                restricted.name_person(d, email, action == "name", who, role, (person or {}).get("name") or "")
            elif action in ("messages_on", "messages_off"):
                restricted.set_messages(d, action == "messages_on", reason, who, role)
            else:
                raise ValueError("Choose what to change: restrict the case, who may open it, or automatic messages.")
        self._requery(d)  # the case's "restricted" in the query layer
        self.forget_feeds()  # who may open the case changed: no calendar built before it keeps showing what it no longer may
        if self.accounts is not None and user:
            # restricted: whether the case was restricted before or after this change, so the row of lifting a restriction is marked too
            self.accounts.log("case_access", user["email"], client=client_id, action=action, restricted=was or restricted.is_restricted(d),
                              **({"person": email} if email else {}))
        return self.access_state(client_id, user)

    # -- Getting started (src/getting_started.py; the attorney's) ------------------------------------

    def getting_started(self, user: dict | None) -> dict:
        import getting_started

        if self.accounts is not None and (user or {}).get("role") != "attorney":
            raise PermissionError("Getting started is the attorney's.")
        return getting_started.build(self, user)

    def getting_started_seen(self, user: dict | None) -> dict:
        import getting_started

        self.getting_started(user)
        getting_started.mark_seen(user)
        return {"ok": True}

    # -- staff accounts on the Settings page (review/auth.py; the attorney's) ------------------------

    def staff(self, user: dict | None) -> dict:
        """The Staff section: every account, its role, on or off, whether it still has its one-time password and whether
        its authenticator app is set up; and the firm's second-factor choices (who must use a code, remembering a device),
        with who set them last."""
        import settings
        from review.auth import second_factor

        if self.accounts is None:
            raise LookupError("This app runs without staff accounts: nobody signs in.")
        if (user or {}).get("role") != "attorney":
            raise PermissionError("Staff accounts are the attorney's to manage.")
        saved = settings.load().get("sign_in") or {}
        return {"people": self.accounts.users(), "roles": list(ROLES), "me": user["email"],
                "second_factor": second_factor() | {"updated_by": saved.get("updated_by"), "updated_at": saved.get("updated_at")}}

    # -- the calendar (src/calendar_feed.py, src/closures.py, src/deadlines_set.py, src/staff_reminders.py) -----------------------------

    def _firm_file(self, name: str) -> Path:
        """A firm-level file of this installation: in data/, beside the case folders' folder."""
        return self.data_root.parent / name

    def feeds(self):
        from calendar_feed import FILE, Feeds

        with self._log_lock:
            if getattr(self, "_feeds", None) is None:
                import calendar_feed

                self._feed_cache, self._feed_rows, self._feed_stamps = calendar_feed.Cache(), calendar_feed.Cache(), calendar_feed.Stamps()
                self._feeds = Feeds(self._firm_file(FILE))
        return self._feeds

    def forget_feeds(self) -> None:
        """Who may open a case, or someone's role, changed: the built calendars go now, not at the end of their minute."""
        self.feeds()
        self._feed_cache.clear()
        self._feed_rows.clear()

    def _people(self) -> list[dict]:
        """The staff accounts that can be named as responsible for a deadline: [{email, name, role}] (none without staff accounts). For the server's own use:
        the case page is sent _people_public()."""
        return [{"email": u["email"], "name": u["name"], "role": u["role"]} for u in self.accounts.users() if u.get("active")] if self.accounts else []

    @staticmethod
    def person_id(email: str) -> str:
        """A staff member's id on the case page's choices: a hash of their e-mail, so a page never carries a colleague's address."""
        import hashlib

        return hashlib.sha256(("person|" + str(email).strip().lower()).encode()).hexdigest()[:12]

    def _people_public(self) -> list[dict]:
        return [{"id": self.person_id(p["email"]), "name": p["name"], "role": p["role"]} for p in self._people()]

    def _person_email(self, chosen) -> str:
        """The e-mail of the staff member a case page chose (by id; an e-mail is taken as it is), or "" for nobody."""
        chosen = str(chosen or "").strip()
        if not chosen:
            return ""
        return next((p["email"] for p in self._people() if chosen in (self.person_id(p["email"]), p["email"])), chosen)

    def _needs_account(self, user: dict | None) -> dict:
        if self.accounts is None or user is None:
            raise LookupError("The calendar needs staff accounts: it is made for each person who signs in.")
        return user

    def calendar_state(self, user: dict | None) -> dict:
        """Settings, My calendar: whether the person has an address (when it was made, never the address: it is shown once, when made), whether the
        firm's address is theirs to make (an attorney's), and their reminders switch."""
        import staff_reminders

        user = self._needs_account(user)
        made = self.feeds().status(user["email"])
        return {"person": made["person"], "firm": made["firm"] if user["role"] == "attorney" else None, "can_firm": user["role"] == "attorney",
                "reminders": staff_reminders.wants(self._firm_file(staff_reminders.FILE), user["email"])}

    def calendar_change(self, body: dict, user: dict | None) -> dict:
        """{action: make (kind: person | firm) | revoke (kind) | reminders (on)}. A new address revokes the old one of its kind; the address is in this
        answer only (path), once. The ledger and the access log say an address was made or revoked, never the address."""
        import staff_reminders

        user = self._needs_account(user)
        action, kind = str(body.get("action") or ""), str(body.get("kind") or "person")
        if kind == "firm" and user["role"] != "attorney":
            raise PermissionError("The firm's calendar address is the attorney's.")
        out: dict = {}
        if action == "make":
            token = self.feeds().make(user["email"], kind, user["name"])
            self.accounts.log("calendar_address_made", user["email"], kind=kind, by=user["email"])
            out["path"], out["kind"] = f"/calendar/{token}.ics", kind
        elif action == "revoke":
            if self.feeds().revoke(user["email"], kind):
                self.accounts.log("calendar_address_revoked", user["email"], kind=kind, by=user["email"])
        elif action == "reminders":
            staff_reminders.set_consent(self._firm_file(staff_reminders.FILE), user["email"], bool(body.get("on")), user["name"])
        else:
            raise ValueError("Choose what to do: make an address, turn it off, or switch reminders.")
        return out | self.calendar_state(user)

    def _calendar_rows(self) -> list[dict]:
        """Every case's row with its deadlines, restricted cases included and marked: each subscriber's own gate decides what they are shown."""
        from calendar_feed import is_open
        from review.overview import overview

        if self.scaled:  # the roster's rows (each knows whether it has ended): no look at every case's folder once a minute
            return [r for r in self.roster.rows(None) if not r.get("end")] + self.prospect_tasks(None)  # an open prospect's tasks are on it too (src/prospects.py), each subscriber's own gate deciding
        rows = self._visible_rows(overview(self.data_root, self.field_map, self.template, self.catalog, self.portal_root)["clients"], None)
        # a case that has ended is on no calendar (src/engagement.py); an open prospect's tasks are (src/prospects.py), each subscriber's own gate deciding below
        return [r for r in rows if is_open(self.data_root / r["id"])] + self.prospect_tasks(None)

    def _shared_rows(self) -> list[dict]:
        """_calendar_rows once for every person and every poll in the same minute, built by one request while the others wait (single flight)."""
        self.feeds()
        return self._feed_rows.get(("rows",), self._calendar_rows)

    def calendar_file(self, token: str, base_url: str, address: str = "") -> bytes | None:
        """The calendar file the token opens, or None (the one 404 for any token that is not a current person's: unknown, revoked, an account turned off,
        an attorney's firm address of someone who is no longer an attorney). A request is one access-log row a day per token."""
        import calendar_feed as feed

        if self.accounts is None:
            return None
        found = self.feeds().find(token)
        user = next((u for u in self.accounts.users() if found and u["email"] == found["email"] and u.get("active")), None)
        if not found or user is None or (found["kind"] == "firm" and user["role"] != "attorney"):
            if found:  # an account turned off, or an attorney's firm address of someone who is not one: the address ends now, and a later turn-on does not bring it back
                self.feeds().revoke(found["email"], found["kind"])
            return None
        if self.feeds().first_today(found["hash"]):
            self.accounts.log("calendar_feed", user["email"], kind=found["kind"])

        def may_see(person: dict, case: str, deadline: dict) -> bool:
            if case.startswith("prospect:"):  # a prospect's task: the same rule as a case's, by the prospect's own folder (src/restricted.py)
                import prospects

                d = prospects.dir_of(self.data_root, case[len("prospect:"):])
                return d is not None and self._prospect_visible(person, d)
            if not self._listed_to(person, case):
                return False
            if (deadline.get("expiry") or {}).get("confidential"):  # a confidential document's date: attorneys and the staff named on the case
                d = self._case_folder(case)
                return d is None or (self.roster.sees_confidential(person, case) if self.scaled else restricted.sees_confidential(person, d))
            return True

        def make() -> bytes:
            events_ = feed.build(self._shared_rows(), user, found["kind"], may_see, base_url)
            name = (f"{__import__('settings').firm_name() or 'Case Review'} deadlines" if found["kind"] == "firm" else f"Deadlines for {user['name']}")
            return feed.ics(events_, name, stamps=self._feed_stamps)

        return self._feed_cache.get((user["email"], found["kind"], user["role"], base_url), make)

    def month(self, q: dict, user: dict | None) -> dict:
        """What's due, Month view: one month as a plain grid (Monday first, the firm's zone), each day with its events and the closures
        (src/closures.py). q: month (YYYY-MM, default this month), scope (me | firm). The cases this person may see only, a restricted case they
        may not open left out (like What's due). "me": the deadlines on the person's own list (calendar_feed.mine); "firm": every one."""
        import calendar as cal

        import calendar_feed as feed
        import closures
        import journey
        from review.overview import PASSED_DAYS, history_once_missed

        raw = str(q.get("month") or clock.today().strftime("%Y-%m"))
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", raw):
            raise ValueError("Choose a month.")
        year, month = int(raw[:4]), int(raw[5:])
        if not 2000 <= year <= 2100:
            raise ValueError("Choose a month.")
        scope = "firm" if str(q.get("scope") or "me") == "firm" or user is None else "me"
        today, cfg = clock.today(), journey.settings()["deadlines"]
        first = date(year, month, 1)
        start = first - timedelta(days=first.weekday())  # Monday first
        weeks_n = len(cal.Calendar(firstweekday=0).monthdayscalendar(year, month))
        end = start + timedelta(days=7 * weeks_n - 1)
        shut = closures.by_day(start, end)
        who = self._who(None, user)
        rows = (self._open_rows(self._unrestricted(self._rows(who), who)) if self.scaled else
                [r for r in self._unrestricted(self._rows(who), who) if feed.is_open(self.data_root / r["id"])]) + self.prospect_tasks(user)  # a case that has ended is not on the month; a prospect's tasks are
        days: dict[str, list[dict]] = {}
        for row in rows:
            name = (row.get("summary") or {}).get("name")
            for d in ((row.get("journey") or {}).get("deadlines") or []):
                try:
                    when = date.fromisoformat(str(d["date"])[:10])
                except (KeyError, ValueError):
                    continue
                if not start <= when <= end or (d.get("owner") == "client" and when < today) or (scope == "me" and user and not feed.mine(d, user)):
                    continue
                left = (when - today).days
                appt = d.get("appt") or {}
                days.setdefault(when.isoformat(), []).append({
                    "client": row["id"], "name": name, "what": d["what"], "owner": d["owner"], "who_name": d.get("who_name"), "time": appt.get("time"),
                    "level": "passed" if left < -PASSED_DAYS and history_once_missed(d["id"]) else journey._level(left, cfg), "days_left": left, "date": when.isoformat()})
        grid = []
        for w in range(weeks_n):
            week = []
            for i in range(7):
                day = start + timedelta(days=7 * w + i)
                items = sorted(days.get(day.isoformat(), []), key=lambda e: (e["time"] or "", e["what"]))
                week.append({"date": day.isoformat(), "day": day.day, "in_month": day.month == month, "today": day == today, "weekend": i >= 5,
                             "closures": shut.get(day.isoformat(), []), "events": items})
            grid.append(week)
        prev, nxt = (first - timedelta(days=1)).replace(day=1), (first + timedelta(days=32)).replace(day=1)
        return {"month": raw, "label": first.strftime("%B %Y"), "scope": scope, "weeks": grid, "previous": prev.strftime("%Y-%m"), "next": nxt.strftime("%Y-%m"),
                "this_month": today.strftime("%Y-%m"), "notes": closures.notes(year), "follows": closures.follows(), "sources": closures.sources(), "weekdays": [cal.day_abbr[i] for i in range(7)]}

    def deadline_change(self, client_id: str, body: dict, user: dict | None) -> dict:
        """The case page's deadlines: {action: add (title, date, who, note) | assign (id, who) | done (id)}. who is a staff member's email (the person
        responsible); a deadline a person set is never deleted (done hides it). Returns the case's journey."""
        import deadlines_set

        d = self.client_dir(client_id)
        who = str(body.get("reviewer") or "").strip()
        action = str(body.get("action") or "")
        people = self._people()
        with self._lock:
            if action == "add":
                deadlines_set.add(d, body.get("title"), body.get("date"), self._person_email(body.get("who")), body.get("note"), who, people, body.get("who_name"))
            elif action == "assign":
                deadlines_set.assign(d, body.get("id"), self._person_email(body.get("who")), who, people, body.get("who_name"))
            elif action == "done":
                deadlines_set.done(d, body.get("id"), who)
            else:
                raise ValueError("Choose what to do with the deadline: add it, name who is responsible, or mark it done.")
            self._requery(d)
        return self.journey(client_id)

    # -- the first call: prospects, case notes and tasks, what could this person apply for (src/prospects.py, src/case_notes.py, src/apply_for.py) ----------------------

    def prospect_dir(self, user: dict | None, prospect_id: str) -> Path:
        """The gate every route that opens one prospect passes first: the prospect's folder when this person may open it (restricted.visible_to: the same record and the same rule
        as a case's), else the one answer for a prospect that does not exist and one this person may not open: "unknown client", 404."""
        import prospects

        d = prospects.dir_of(self.data_root, prospect_id)
        if d is None or not self._prospect_visible(user, d):
            raise LookupError("unknown client")
        return d

    def _prospect_visible(self, user: dict | None, d: Path) -> bool:
        """May this person open this prospect: the prospect's own restriction record lets them (restricted.visible_to), and, once the prospect became a client, so does the case's
        (may_open: a protected kind, or an attorney's mark made on the case later, closes the prospect too). A case that cannot be found is closed: nothing is shown on a doubt."""
        import prospects

        if self.accounts is None:
            return True
        if not restricted.visible_to(user, d):
            return False
        became = (prospects.read(d).get("became_client") or {}).get("id")
        return not became or self.may_open(user, became)

    def _prospect_gate(self, user: dict | None):
        """For a list: may this person open this prospect's folder?"""
        return lambda d: self._prospect_visible(user, d)

    def prospects_list(self, q: dict, user: dict | None, csv: bool = False):
        """Prospects (on All clients): the people who called and are not clients yet, a page at a time, or all of them as a CSV file. Only the ones this person may open; none is
        in any client list or count."""
        import prospects

        gate = self._prospect_gate(user)
        stage = str(q.get("stage") or "")
        if stage and stage not in prospects.STAGES:
            raise ValueError("Choose a stage from the list.")
        if csv:
            return prospects.csv_text(self.data_root, self.portal_root, gate, str(q.get("q") or ""), stage)
        out = prospects.listing(self.data_root, self.portal_root, gate, str(q.get("q") or ""), stage, self._page_of(q))
        return out | {"can_add": True}

    def _notes_block(self, d: Path, user: dict | None = None) -> dict:
        """The notes (an attorney-only note for attorneys only) and the tasks."""
        import case_notes

        attorney = self.accounts is None or (user or {}).get("role") == "attorney"
        return {"notes": case_notes.notes(d, attorney), "tasks": case_notes.tasks(d), "staff": self._people_public(), "today": clock.today().isoformat(),
                "can_mark_attorney_only": attorney}

    def prospect_view(self, prospect_id: str, user: dict | None) -> dict:
        """One prospect's page: the call, the stage, the answers and who gave them, the notes and tasks, and the questions of what could this person apply for with the answers."""
        import apply_for
        import prospects

        d = self.prospect_dir(user, prospect_id)
        with self._lock:
            out = prospects.view(self.data_root, self.portal_root, d.name)
        role = (user or {}).get("role") if self.accounts is not None else None
        ended = bool(out.get("declined") or out.get("became"))
        return out | self._notes_block(d, user) | {"apply_for": apply_for.view(d), "can_decline": role != "paralegal", "can_show_link": role != "paralegal",
                                                          "prefill": None if ended else prospects.prefill(self.data_root, self.portal_root, d.name),
                                                          "letters": self._prospect_letters(d)}

    @staticmethod
    def _prospect_letters(d: Path) -> list[dict]:
        import engagement

        return [{"id": x["id"], "name": engagement.KINDS.get(x["kind"], x["kind"]), "made_at": x.get("made_at"), "made_by": x.get("made_by")} for x in engagement.read(d).get("letters") or []]

    def prospect_new(self, body: dict, user: dict | None) -> dict:
        """New prospect: the first call recorded (name, how to reach them, language, office, how they heard, the day, who took it, the first note, the kind of case if known)."""
        import prospects

        role = (user or {}).get("role") if self.accounts is not None else None
        with self._lock:
            rec = prospects.create(self.data_root, body, str(body.get("reviewer") or ""), role, self.portal_root)
        return {"id": rec["id"], "name": rec["name"]} | ({"restricted": True, "law": rec["law"], "note": rec["note"]} if rec.get("restricted") else {})

    def prospect_change(self, body: dict, user: dict | None) -> dict:
        """What staff do to a prospect: a note, a task (add, name who is responsible, done), an answer to a what-could-apply-for question, send the questions or show the link, type
        the answers in, mark it ready for the attorney, decline it (the attorney's). The prospect is opened through prospect_dir first, so a restricted one is unknown to anyone
        who may not open it, whatever the action."""
        import apply_for
        import case_notes
        import prospects

        d = self.prospect_dir(user, str(body.get("prospect") or ""))
        action, who = str(body.get("action") or ""), str(body.get("reviewer") or "").strip()
        role = (user or {}).get("role") if self.accounts is not None else None
        out: dict = {}
        with self._lock:
            if action == "note":
                case_notes.add_note(d, body.get("text"), who, role, prospect=True, corrects=str(body.get("corrects") or "") or None, attorney_only=body.get("attorney_only") is True)
            elif action == "task":
                case_notes.add_task(d, body.get("title"), body.get("date"), self._person_email(body.get("who")), body.get("note"), who, self._people(), body.get("who_name"), role,
                                    prospect=True)
            elif action == "task_assign":
                case_notes.assign_task(d, body.get("id"), self._person_email(body.get("who")), who, self._people(), body.get("who_name"), role, prospect=True)
            elif action == "task_done":
                case_notes.finish_task(d, body.get("id"), who, role, prospect=True)
            elif action == "apply":
                apply_for.answer(d, str(body.get("question") or ""), body.get("answer"), who, role, prospect=True)
            elif action == "send":
                out = prospects.send_questions(self.data_root, self._need_portal(), d.name, who, role, again=bool(body.get("again")))
            elif action == "link":
                out = prospects.show_link(self.data_root, self._need_portal(), d.name, who, os.environ.get("PORTAL_BASE_URL", "http://localhost:8600"), role, actor_email=(user or {}).get("email"))
            elif action == "answers":
                prospects.save_answers(self.data_root, self._need_portal(), d.name, body.get("answers") if isinstance(body.get("answers"), dict) else {}, who, role)
            elif action == "waiting":
                prospects.mark_waiting(self.data_root, d.name, who, role)
            elif action == "decline":
                out = {"declined": True}
                prospects.decline(self.data_root, self._need_portal(), d.name, who, role, str(body.get("reason") or ""), str(body.get("additions") or ""))
            else:
                raise ValueError("Choose what to do with this prospect.")
        return self.prospect_view(d.name, user) | ({"link": out} if action == "link" else {"delivery": out.get("delivery")} if action == "send" else {})

    def _need_portal(self) -> Path:
        if self.portal_root is None:
            raise ValueError("This installation has no client portal: the first questions can't be sent.")
        return Path(self.portal_root)

    def prospect_letter(self, prospect_id: str, letter_id: str | None, user: dict | None) -> Path:
        """The non-engagement letter made when the firm declined the prospect (the PDF)."""
        import engagement

        d = self.prospect_dir(user, prospect_id)
        if not re.fullmatch(r"L\d{1,6}", str(letter_id or "")):
            raise ValueError("bad letter")
        return engagement.letter_pdf(d, str(letter_id))

    def prospect_tasks(self, user: dict | None) -> list[dict]:
        """The tasks that no case timeline holds, as list rows, those this person may open: an open prospect's (src/prospects.py work_rows) and a client's who has no case file yet
        (src/case_notes.py unprocessed_rows). For My work, What's due, the month view and the calendar, beside the cases' own deadlines."""
        import case_notes
        import journey
        import prospects

        cfg, today = journey.settings()["deadlines"], clock.today()
        rows = case_notes.unprocessed_rows(self.data_root, self.portal_root, today, cfg, lambda d: self.may_open(user, d.name) if self.accounts is not None else True,
                                           candidates=self.roster.task_folders() if self.scaled else None)  # (as installed the lists' copy says which folders hold tasks)
        if prospects.folder(self.data_root).is_dir():
            rows += prospects.work_rows(self.data_root, today, cfg, self._prospect_gate(user))
        return rows

    def case_notes(self, client_id: str, user: dict | None = None) -> dict:
        """A case's notes and tasks (the route is gated by may_open like every route that opens one case), and the first call the client began with, when there was one."""
        return self._notes_block(self._engagement_dir(client_id), user) | {"first_call": self._first_call(client_id, user)}

    def case_notes_change(self, client_id: str, body: dict, user: dict | None) -> dict:
        """A note on the case, or a task (add, name who is responsible, done): who and when are kept, a ledger row each; nothing is edited or deleted."""
        import case_notes

        d = self._engagement_dir(client_id, create=True)
        action, who = str(body.get("action") or ""), str(body.get("reviewer") or "").strip()
        role = (user or {}).get("role") if self.accounts is not None else None
        with self._lock:
            if action == "note":
                case_notes.add_note(d, body.get("text"), who, role, corrects=str(body.get("corrects") or "") or None, attorney_only=body.get("attorney_only") is True)
            elif action == "task":
                case_notes.add_task(d, body.get("title"), body.get("date"), self._person_email(body.get("who")), body.get("note"), who, self._people(), body.get("who_name"), role)
            elif action == "task_assign":
                case_notes.assign_task(d, body.get("id"), self._person_email(body.get("who")), who, self._people(), body.get("who_name"), role)
            elif action == "task_done":
                case_notes.finish_task(d, body.get("id"), who, role)
            else:
                raise ValueError("Choose what to do: add a note, add a task, name who is responsible, or mark a task done.")
            self._requery(d)
        return self.case_notes(client_id, user)

    def apply_for_view(self, client_id: str) -> dict:
        """What could this person apply for, on a case: the questions and the answers so far (never a conclusion)."""
        import apply_for

        return apply_for.view(self._engagement_dir(client_id))

    def apply_for_answer(self, client_id: str, body: dict, user: dict | None) -> dict:
        import apply_for

        d = self._engagement_dir(client_id, create=True)
        role = (user or {}).get("role") if self.accounts is not None else None
        with self._lock:
            apply_for.answer(d, str(body.get("question") or ""), body.get("answer"), str(body.get("reviewer") or ""), role)
        return apply_for.view(d)

    def code_issuer(self) -> str:
        """The name the authenticator app shows above the code: the firm's (the main office on the Settings page)."""
        import settings

        name = settings.firm_name()  # what the firm saved: no firm's name is built in (the shipped defaults are not a name)
        return f"{name} Case Review" if name else "Case Review"

    def staff_change(self, body: dict, user: dict | None) -> dict:
        """{action: add (email, name, role) | disable | enable | reset | role (role), email}, or {action: second_factor, everyone,
        remember}. A new or reset account's one-time password is in this answer only (it is stored hashed: shown once); a reset
        also clears the person's authenticator app. Turning someone off ends their sessions at once, as the command line does
        (Accounts.update). Every change goes to the access log with who made it."""
        self.staff(user)
        action, email = str(body.get("action") or ""), str(body.get("email") or "").strip().lower()
        out: dict = {}
        if action == "second_factor":  # kept with the Sign-in settings (who and when, the earlier values) and in the access log
            import settings

            from review.auth import second_factor

            everyone, remember = bool(body.get("everyone")), bool(body.get("remember"))
            was = second_factor()
            with self._lock:
                settings.save("sign_in", {"code_everyone": "yes" if everyone else "no", "remember_device": "yes" if remember else "no"},
                              user["name"])
            self.accounts.log("second_factor_changed", user["email"], everyone=everyone, remember=remember, by=user["email"])
            if not remember:  # off: the browsers remembered so far ask for the code again too
                self.accounts.forget_devices(by=user["email"])
            if everyone and not was["everyone"]:  # on: whoever is signed in without the app is signed out, and sets it up next
                out["signed_out"] = self.accounts.sign_out_without_code(by=user["email"])
        elif action == "add":
            out = {"one_time_password": self.accounts.add(email, str(body.get("name") or ""), str(body.get("role") or ""), by=user["email"]), "for": email}
        else:
            if not any(u["email"] == email for u in self.accounts.users()):
                raise LookupError(f"No account for {email}.")
            if email == user["email"]:
                raise ValueError("Ask another attorney to change your own account.")
            if action in ("disable", "enable"):
                self.accounts.update(email, by=user["email"], active=action == "enable")
                if action == "disable":  # a person turned off keeps no calendar address: turning them on again does not bring one back
                    for kind in ("person", "firm"):
                        self.feeds().revoke(email, kind)
                self.forget_feeds()
            elif action == "reset":
                out = {"one_time_password": self.accounts.reset(email, by=user["email"]), "for": email}
            elif action == "role":
                self.accounts.update(email, by=user["email"], role=str(body.get("role") or ""))
                if str(body.get("role") or "") != "attorney":  # no longer an attorney: no firm address
                    self.feeds().revoke(email, "firm")
                self.forget_feeds()
            else:
                raise ValueError("Choose what to change: add, turn off, turn on, reset the password, or change the role.")
        return out | self.staff(user)

    def _portal_note(self, client_id: str) -> dict:
        """For a client in the portal: the questionnaire they answer (and the ones it can change to), whether they were invited and when."""
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            return {}
        from review import front_desk

        store = self._portal()
        profile = store.profile(client_id)
        return {"portal": {"filing": profile.get("filing") or "i485", "questionnaires": front_desk.questionnaires(), "status": profile.get("status"),
                           "invited_at": profile.get("invited_at"), "last_invite": profile.get("last_invite"), "last_invite_attempt": profile.get("last_invite_attempt"), "last_invite_by": profile.get("last_invite_by"),
                           "filing_changed": profile.get("filing_changed"),
                           "change_warning": front_desk.change_warning(store, client_id)}}  # asked before the questionnaire changes

    def _note_foreign(self, client_dir: Path, out: dict) -> None:
        """On each source that is a foreign-language document: its language and the summary built from what the extractors found
        in it (src/translation.py), for the card's "Where this answer came from"."""
        import translation

        foreign = translation.summaries(client_dir)
        if not foreign:
            return
        for card in out.get("cards") or []:
            for fact in (card.get("facts") or []) + (card.get("context") or []):
                for s in fact.get("sources") or []:
                    if s.get("doc") in foreign:
                        s["foreign"] = {k: foreign[s["doc"]][k] for k in ("language_name", "summary")}

    def _note_client_not_asked(self, client_id: str, out: dict) -> None:
        """On each decision the client was spared a question for: why (portal/engine.py records it when it skips the task)."""
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            return
        from portal.store import PortalStore

        skipped = {r["fact"]: r["why"] for r in PortalStore(self.portal_root).skipped_tasks(client_id)}
        for done in out["done"]:
            why = next((skipped[k] for k in done["decision"]["item"]["facts"] if k in skipped), None)
            if why:
                done["client_not_asked"] = why

    def _note_client_answered(self, client_id: str, out: dict) -> None:
        """On each card the office asked the client about: the question, how it reached them ("Asked in Portuguese: ...")
        and their answer in words, for the card's "Where this answer came from" (the same as a portal answer)."""
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            return
        from portal.questions import asked_line, reply_words
        from portal.store import PortalStore

        answered = [r for r in PortalStore(self.portal_root).requests(client_id) if r["status"] == "answered" and r.get("facts") and r.get("reply")]
        for card in out.get("cards") or []:
            keys = {f.get("key") for f in card.get("facts") or []}
            rows = [{"question": r.get("text_en") or r["text"], "answer": reply_words(r), "by": r.get("by"), "answered_at": r.get("answered_at"),
                     "asked_line": asked_line(r)} for r in answered if keys & set(r["facts"])]
            if rows:
                card["asked"] = rows

    # -- the agreement, declining, withdrawing, closing (src/engagement.py) ------------------------------------------------------

    def _engagement_dir(self, client_id: str, create: bool = False) -> Path:
        """Where a client's agreement and end are kept: the case's folder; for a prospect not processed yet (a client in the portal, or a folder that
        holds only the conflict check or the restriction record) its folder in the clients folder, made when a record is first written (create).
        The route's gate (may_open) has already passed; an id that names nothing is unknown."""
        d = self._case_folder(client_id) or self._held_folder(client_id)
        if d is not None:
            return d
        if not client_id or client_id in (".", "..") or "/" in client_id or "\\" in client_id or "\0" in client_id:
            raise LookupError("unknown client")
        d = self.data_root / client_id
        if d.is_dir() and client_id in {x.name for x in self.data_root.iterdir()}:
            return d
        if self._in_portal(client_id):
            if create:
                d.mkdir(exist_ok=True)
            return d
        raise LookupError("unknown client")

    def engagement_view(self, client_id: str, user: dict | None = None) -> dict:
        """The case page's Agreement and closing panel; a signature the client made in the portal is put on the case first."""
        import engagement

        d = self._engagement_dir(client_id)
        view = engagement.view(d, self.portal_root, (user or {}).get("role"))
        suggestions = []
        reason = "No current case-path suggestion is available. Choose the covered filings explicitly."
        if (d / "fact_graph.json").is_file():
            import journey
            import packet
            try:
                suggestions = [{"id": row["filing"], "label": packet.filing_title(row["filing"])}
                               for row in journey.journey(d)["next_filings"]
                               if row.get("now") and row.get("filing") in packet.FILINGS]
                if suggestions:
                    reason = "Suggested by the current case path; confirm each covered filing explicitly."
            except (OSError, ValueError, LookupError):
                reason = "The current case path could not be read. Choose the covered filings explicitly."
        return view | {"filing_suggestions": suggestions, "filing_suggestion_reason": reason}

    @staticmethod
    def _agreement_preview_digest(preview: dict) -> str:
        # The template hash alone does not bind the filled case/terms/language.
        import hashlib
        return hashlib.sha256(json.dumps(preview, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

    def engagement_preview(self, client_id: str, body: dict) -> dict:
        """Protected, unsaved exact letter paragraphs; no preparation/send approval."""
        import engagement
        import packet
        filings = body.get("filings")
        if not isinstance(filings, list) or not filings or any(not isinstance(f, str) or f not in packet.FILINGS for f in filings):
            raise ValueError("Choose valid covered filings explicitly.")
        values = {}
        for name in ("fee", "government_fees", "additions"):
            value = body.get(name, "")
            if not isinstance(value, str):
                raise ValueError("Agreement terms must be text.")
            values[name] = value
        preview = engagement.preview_agreement(self._engagement_dir(client_id), filings, values["fee"],
                                               values["government_fees"], values["additions"], self.portal_root)
        return preview | {"preview_sha256": self._agreement_preview_digest(preview)}

    def engagement_change(self, client_id: str, body: dict, role: str | None = None, *, session_token=None) -> dict:
        """Make the agreement, send it to be signed, counter-sign it; decline, withdraw, transfer, close or reopen the case (the attorney's);
        make the client's file and record it handed over (the attorney's). Every change records who and when, and a ledger row."""
        import engagement

        d, who, action = self._engagement_dir(client_id, create=True), str(body.get("reviewer") or ""), str(body.get("action") or "")
        portal = self.portal_root
        with self._lock:
            if self.accounts is not None and session_token is not None:
                current_actor = self.accounts.session_user(session_token)
                if current_actor is None:
                    raise PermissionError("Sign in again before making a case determination.")
                if not self.may_open(current_actor, client_id):
                    raise LookupError("unknown client")
                who, role = current_actor["name"], current_actor["role"]
                events.set_actor(who, role)
            if action == "make":
                if "preview_sha256" in body:
                    import hmac
                    expected = body["preview_sha256"]
                    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                        raise ValueError("Refresh the unsaved agreement preview before making it.")
                    # The existing handler case lock and app lock cover the
                    # current recheck and creation; no saved approval token.
                    current = self.engagement_preview(client_id, body)
                    if not hmac.compare_digest(expected, current["preview_sha256"]):
                        raise ValueError("The agreement preview changed. Refresh it and review the current letter before making it.")
                out = engagement.make_agreement(d, [str(f) for f in body.get("filings") or []], str(body.get("fee") or ""), who, role,
                                                str(body.get("government_fees") or ""), str(body.get("additions") or ""), portal)
            elif action == "send":
                out = engagement.send(d, str(body.get("letter") or ""), who, role, portal)
            elif action == "countersign":
                out = engagement.countersign(d, str(body.get("letter") or ""), str(body.get("typed_name") or ""), who, role, portal)
            elif action == "end":
                out = engagement.end(d, str(body.get("state") or ""), who, role, reason=str(body.get("reason") or ""), additions=str(body.get("additions") or ""),
                                     matter=str(body.get("matter") or ""), returned=str(body.get("returned") or ""), how=str(body.get("how") or ""),
                                     ended_by=str(body.get("ended_by") or ""), portal_root=portal)
            elif action == "reopen":
                out = engagement.reopen(d, str(body.get("reason") or ""), who, role, portal)
            elif action in ("file_policy_facts", "file_policy_approve", "inventory_review", "recipient_save", "retention_review", "destruction_approve"):
                import client_file_policy as policy
                actor = {"who": who, "role": role, "portal_root": portal}
                revision, snapshot = body.get("expected_revision"), body.get("expected_snapshot_sha256")
                if action == "file_policy_facts":
                    policy.save_facts(d, revision, body.get("facts"), **actor)
                elif action == "file_policy_approve":
                    policy.approve(d, revision, snapshot, body.get("source_reviewed"), **actor)
                elif action == "inventory_review":
                    policy.approve_inventory(d, revision, snapshot, body.get("expected_inventory_sha256"), body.get("decisions"), **actor)
                elif action == "recipient_save":
                    policy.save_recipient(d, revision, snapshot, body.get("recipient"), **actor)
                elif action == "retention_review":
                    policy.approve_retention(d, revision, snapshot, body.get("keep_until"), body.get("determination"), **actor)
                else:
                    policy.approve_destruction(d, revision, snapshot, body.get("protections_reviewed"), body.get("determination"), **actor)
                out = engagement.view(d, portal, role)
            elif action == "file":
                out = engagement.export_file(d, self.data_root, who, role, portal, str(body.get("expected_binding_sha256") or ""))
            elif action == "file_approve":
                out = engagement.approve_file(d, str(body.get("sha256") or ""), who, role, portal, str(body.get("expected_binding_sha256") or ""))
            elif action == "consent_approve":
                out = engagement.approve_consent(d, str(body.get("text") or ""), str(body.get("translator") or ""), who, role, portal)
            elif action == "returned":
                out = engagement.file_returned(d, str(body.get("on") or ""), str(body.get("method") or ""), who, role, portal,
                    str(body.get("sha256") or ""), str(body.get("receipt_reference") or ""), str(body.get("expected_binding_sha256") or ""), str(body.get("recipient_sha256") or ""))
            else:
                raise ValueError("Unknown change.")
        self._requery(d)
        return out

    def engagement_paper(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """The client signed the agreement on paper: the scan and the date it was signed."""
        import base64
        import binascii

        import engagement

        try:
            data = base64.b64decode(str(body.get("data") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("That file didn't arrive whole: add it again.") from None
        with self._lock:
            return engagement.paper(self._engagement_dir(client_id), str(body.get("letter") or ""), data, str(body.get("on") or ""),
                                    str(body.get("reviewer") or ""), role, self.portal_root)

    def engagement_pdf(self, client_id: str, letter_id: str | None, paper_copy: bool = False) -> Path:
        import engagement

        if not re.fullmatch(r"L\d{1,6}", str(letter_id or "")):
            raise ValueError("bad letter")
        return engagement.letter_pdf(self._engagement_dir(client_id), str(letter_id), paper_copy)

    # -- the fee waiver request, Form EOIR-26A (src/eoir26a.py) ---------------------------------------------------------------------

    def eoir26a_view(self, client_id: str, user: dict | None = None) -> dict:
        """The case page's Fee waiver request card: the client's figures with their sources, the arithmetic, item 4, the signing and the attestation."""
        import eoir26a

        return eoir26a.view(self._engagement_dir(client_id), self.portal_root, (user or {}).get("role"))

    def eoir26a_change(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """Send the questions to the client; type a figure; edit, approve or take back item 4; open the signing (the attorney's); void a signature (the
        attorney's); the attorney's attestation. Every change records who and when, and a ledger row."""
        import eoir26a

        d, who, action = self._engagement_dir(client_id, create=True), str(body.get("reviewer") or ""), str(body.get("action") or "")
        portal = self.portal_root
        with self._lock:
            if action == "send":
                out = eoir26a.send(d, who, role, portal)
            elif action == "figures":
                out = eoir26a.set_figures(d, body.get("values") or {}, who, role, portal)
            elif action == "edit_sentence":
                out = eoir26a.edit_sentence(d, str(body.get("text") or ""), who, role)
            elif action == "approve_sentence":
                out = eoir26a.approve_sentence(d, str(body.get("text") or ""), bool(body.get("blank")), who, role)
            elif action == "undo_sentence":
                out = eoir26a.undo_sentence(d, who, role)
            elif action == "open":
                out = eoir26a.open_signing(d, bool(body.get("drawn")), who, role, portal)
            elif action == "reopen":
                out = eoir26a.reopen(d, str(body.get("why") or ""), who, role, portal)
            elif action == "attest":
                out = eoir26a.attest(d, str(body.get("typed_name") or ""), who, role, portal, bool(body.get("paper_page_2")))
            else:
                raise ValueError("Unknown change.")
        self._requery(d)
        return out

    # -- the explanations of Part 9's Yes answers on the form's own Part 14 page (src/part14_explain.py) -----------------------------------------

    def explain_view(self, client_id: str, user: dict | None = None) -> dict:
        """The case page's Explain the Yes answers card: one entry per Yes the form says to explain, its suggestion with each slot's source, the text,
        the approval, and what holds the packet."""
        import part14_explain
        import wordings

        with wordings.reading_as(lambda case: self.may_open(user, case)):  # the firm's wordings as this reader may see them (no restricted case they may not open)
            return part14_explain.view(self.client_dir(client_id), (user or {}).get("role"))

    def explain_change(self, client_id: str, body: dict, role: str | None = None, user: dict | None = None) -> dict:
        import wordings

        with wordings.reading_as(lambda case: self.may_open(user, case)):
            return self._explain_change(client_id, body, role)

    def _explain_change(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """Edit an explanation, go back to the suggestion, approve it or take the approval back (an attorney's), and the grammar helper. Every change
        records who and when, and a ledger row."""
        import part14_explain as px

        d, who, action, key = self.client_dir(client_id), str(body.get("reviewer") or ""), str(body.get("action") or ""), str(body.get("key") or "")
        if action == "smooth":  # the local model's turn is not held under the case's lock: the suggestion is saved when it comes back
            out = px.smooth(d, key, who, role)
        elif action == "rank":  # the same: the local model returns a number among the firm's own wordings (src/wordings.py)
            out = px.rank(d, key, who, role)
        elif action == "keep_preview":  # what the library would keep of this text, and what it offers to make a slot: nothing is written
            return px.keep_preview(d, key)
        else:
            with self._lock:
                if action == "edit":
                    out = px.edit(d, key, str(body.get("text") or ""), who, role)
                elif action == "revert":
                    out = px.revert(d, key, who, role)
                elif action == "pick":  # one of the firm's own wordings goes into the text; the pick is recorded
                    out = px.pick(d, key, str(body.get("wording") or ""), who, role)
                elif action == "approve":
                    out = px.approve(d, key, who, role, {str(k): str(v) for k, v in (body.get("slots") or {}).items()} if isinstance(body.get("slots"), dict) else None)
                elif action == "undo":
                    out = px.undo(d, key, who, role)
                elif action == "accept_smoothing":
                    out = px.accept_smoothing(d, key, who, role)
                elif action == "decline_smoothing":
                    out = px.decline_smoothing(d, key, who, role)
                else:
                    raise ValueError("Unknown change.")
        self._requery(d)
        return out

    # -- the firm's wording library (src/wordings.py): Settings, Firm wordings -------------------------------------------------------------------

    def wordings_view(self, user: dict | None) -> dict:
        """Settings, Firm wordings: every wording by answer, the candidates from past filings waiting for the attorney, and those to place. The cases a wording was
        used on are shown only for the cases this person may open (a count to everyone)."""
        import wordings

        out = wordings.library(wordings.root(self.data_root), lambda case: self.may_open(user, case))
        return out | {"can_edit": self.accounts is None or (user or {}).get("role") == "attorney"}

    def wordings_change(self, body: dict, user: dict | None, role: str | None) -> dict:
        """The attorney approves candidates in bulk, edits one, places one on its answer, sets some aside, or retires an approved wording (never deletes)."""
        import wordings

        self._attorneys_only(user, "Changing the firm's wordings")
        base, who, action = wordings.root(self.data_root), str(body.get("reviewer") or ""), str(body.get("action") or "")
        ids = [str(i) for i in body.get("ids") or []] or ([str(body["id"])] if body.get("id") else [])
        with self._lock:
            if action == "approve":
                wordings.approve_candidates(base, ids, who, role, str(body.get("voice") or "") or None)
            elif action == "retire":
                wordings.retire(base, ids, who, role, str(body.get("why") or ""))
            elif action == "discard":
                wordings.discard(base, ids, who, role)
            elif action == "edit":
                wordings.edit_candidate(base, str(body.get("id") or ""), str(body.get("text") or ""), who, role)
            elif action == "place":
                wordings.place(base, str(body.get("id") or ""), str(body.get("key") or ""), who, role)
            else:
                raise ValueError("Unknown change.")
        return self.wordings_view(user)

    def wordings_export(self, user: dict | None) -> bytes:
        """The whole library as one file the attorney can take away (the same files are in the export of the firm's data)."""
        import wordings

        self._attorneys_only(user, "The firm's wordings")
        return (json.dumps(wordings.export_all(wordings.root(self.data_root), lambda case: self.may_open(user, case)), indent=1, ensure_ascii=False) + "\n").encode("utf-8")

    # -- the purge of a case (src/purge.py, brief Q1): the attorney's, step by step ------------------------------------------------------------------------

    def _attorney_count(self) -> int:
        if self.accounts is None:
            return 1
        return sum(1 for u in self.accounts.users() if u.get("role") == "attorney" and u.get("active", True))

    def purge_view(self, client_id: str, user: dict | None) -> dict:
        """The purge screen (the case's Agreement and closing tab, an attorney's): the retention clock, the two steps, the purge asked for and its wait,
        every store that will be emptied, what the ledger keeps, the copies the firm deletes itself, and what is never touched (Clio's own record)."""
        import purge

        self._attorneys_only(user, "The purge of a case")
        d = self.client_dir(client_id)
        rec = purge.entry(self.data_root, client_id)
        me = str((user or {}).get("name") or "")
        waiting = rec if rec and rec.get("state") == "waiting" else None
        steps = purge._case_rec(d)
        return purge.destruction_status(d, self.portal_root) | {"clock": purge.retention_clock(d), "steps": purge.steps_done(d), "contact": steps.get("contact") or [], "contact_how": purge.CONTACT_HOW,
                "file_policy": __import__("client_file_policy").display(d, self.portal_root),
                "originals": purge.originals_list(d), "originals_review": steps.get("originals"), "original_choices": purge.ORIGINAL_CHOICES,
                "purge": waiting, "ready": purge.current_ready(self.data_root, client_id), "can_confirm": bool(waiting and waiting.get("needs_confirm") and not waiting.get("confirmed_by")
                                                                                    and (self.accounts is None or waiting.get("asked_by") != me)),
                "wait_days": purge.wait_days(self.data_root), "attorneys": self._attorney_count(),
                "stores": [{"id": k, "title": t} for k, t in purge.STORES], "ledger_keeps": purge.LEDGER_KEEPS, "copies": purge.copies_left(self.data_root),
                "clio": "The case's link to the practice-management system kept here is removed; the system's own record of the matter (in Clio) is the "
                        "firm's to delete there, and the purge does not touch it."}

    def purge_change(self, client_id: str, body: dict, user: dict | None, role: str | None, *, session_token=None) -> dict:
        """One step of a purge: the attempt to reach the client, the review of the originals, the ask, a second attorney's confirmation, a cancel, or
        "purge now" once its day has come (a job). An attorney's; a paralegal gets 403."""
        import purge

        self._attorneys_only(user, "The purge of a case")
        d = self.client_dir(client_id)
        who, action = str(body.get("reviewer") or ""), str(body.get("action") or "")
        with self._lock:
            if self.accounts is not None and session_token is not None:
                current_actor = self.accounts.session_user(session_token)
                if current_actor is None:
                    raise PermissionError("Sign in again before making a purge determination.")
                if not self.may_open(current_actor, client_id):
                    raise LookupError("unknown client")
                self._attorneys_only(current_actor, "The purge of a case")
                user, who, role = current_actor, current_actor["name"], current_actor["role"]
                events.set_actor(who, role)
            if action == "contact":
                purge.record_contact(d, str(body.get("how") or ""), body.get("on"), str(body.get("note") or ""), who, role)
            elif action == "originals":
                purge.review_originals(d, body.get("items") if isinstance(body.get("items"), list) else [], body.get("none_held") is True, who, role)
            elif action == "ask":
                purge.ask(d, str(body.get("reason") or ""), who, role, self._attorney_count())
            elif action == "confirm":
                purge.confirm(self.data_root, client_id, who, role)
            elif action == "cancel":
                purge.cancel(self.data_root, client_id, who, role, str(body.get("why") or ""))
            elif action == "run":
                purge.submit(self.data_root, client_id, who, self.views_log, self.accounts.log_path if self.accounts is not None else None)
                import jobs

                jobs.ensure_worker(self.data_root, self.portal_root)
                return {"submitted": True}
            else:
                raise ValueError("Choose a step of the purge.")
        return self.purge_view(client_id, user)

    def retention_rules(self, user: dict | None) -> dict:
        """Settings, Keeping closed files: each office's rule (proposed or confirmed), the waiting period, and every purge waiting (an attorney's)."""
        import purge

        self._attorneys_only(user, "Keeping closed files")
        waiting = [{"case": c, "purge_on": e["purge_on"], "needs_confirm": e.get("needs_confirm"), "confirmed_by": e.get("confirmed_by")}
                   for c, e in sorted(purge.waiting(self.data_root).items(), key=lambda x: x[1]["purge_on"])]
        return purge.rules(self.data_root) | {"waiting": waiting, "done": len(purge.purged(self.data_root))}

    def retention_rules_change(self, body: dict, user: dict | None, role: str | None) -> dict:
        import purge

        self._attorneys_only(user, "Keeping closed files")
        who = str(body.get("reviewer") or "")
        with self._lock:
            if body.get("action") == "confirm":
                purge.confirm_rule(self.data_root, str(body.get("office") or ""), body.get("years"), body.get("from_majority") is True, who, role)
            elif body.get("action") == "wait":
                purge.set_wait(self.data_root, body.get("days"), who, role)
            else:
                raise ValueError("Choose what to change: an office's rule, or the waiting period.")
        return self.retention_rules(user)

    _purges_seen: float | None = None

    def after_purges(self) -> None:
        """A purge ran in the job worker: the app's own copies in memory forget the case (the lists' copy, the indexes over the view log, the access
        log and the ledger, the view log's repeat check). Asked at the start of each request: one look at the purge record's time."""
        import purge

        path = purge._home(self.data_root) / purge.PURGES_FILE
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return
        if stamp == self._purges_seen:
            return
        first = self._purges_seen is None
        self._purges_seen = stamp
        gone = purge.purged(self.data_root)
        if first and not gone:
            return
        with self.roster.lock:
            for case in gone:
                if self.roster.entries.pop(case, None) is not None:
                    self.roster.version += 1
                    self.roster.unsaved = True
        for ix in (self.views.index, self.staff_log.index if self.staff_log else None):
            if ix is not None:
                with ix.lock:
                    ix._clear()
        with self.events._lock:
            self.events._indexes.clear()
        self._seen.clear()

    # -- Find across the firm (src/find.py, brief N1): the index of meaning, on the Search page -----------------------------------------------------------

    def find_view(self, user: dict | None) -> dict:
        """Whether Find across the firm is on, whether this person may switch it, and, for an attorney, who asked what and when (the questions' text)
        and how much the index holds. Nothing here names or counts a case."""
        import find

        attorney = self.accounts is None or (user or {}).get("role") == "attorney"
        out = {"on": find.is_on(), "can_switch": attorney, "top": find.TOP, "max": find.MAX_QUESTION}
        if attorney:
            out["asked"] = find.asked(self.data_root)
        return out

    def find(self, body: dict, user: dict | None, role: str | None) -> dict:
        """A question across the firm: the passages closest in meaning, each from a case this person may open, a document they may see, a note
        they may read (src/find.py search: the gate comes before the ranking). Refused while the attorney's switch is off. Every question is a ledger
        row (who, when, its length) and a row in the question log the attorney reads (its text)."""
        import find
        import wordings

        if not find.is_on():
            raise PermissionError("Find across the firm is switched off. An attorney can switch it on under Settings, Drafting and models.")
        question = " ".join(str(body.get("question") or "").split())
        if not re.search(r"\w", question):
            raise ValueError("Type a question first.")
        if len(question) > find.MAX_QUESTION:
            raise ValueError(f"That question is too long ({find.MAX_QUESTION} characters at most).")
        who = self._who(role, user)
        scope = self._scope(who)
        path = find.default_path(self.data_root)
        try:
            ready = find.keep_up(self.data_root, path)  # the cases changed since the last look, in the background; the first build too
        except Exception as exc:  # noqa: BLE001 -- the question reads the index as it is
            sys.stderr.write(f"find index not brought up to date: {type(exc).__name__}\n")
            ready = path.exists()
        gate = (lambda case: self.may_open(user, case)) if self.accounts is not None else None
        base = wordings.root(self.data_root)

        def wording_ok(wording_id: str) -> bool:
            rec = wordings.get(base, wording_id)
            return rec is not None and rec.get("status") == "approved" and not wordings.hidden(rec, gate)

        try:
            found = find.search(question, db_path=path, hidden=scope["hidden"] if self.accounts is not None else (),
                                confidential_cases=scope["confidential"] if self.accounts is not None else None,
                                attorney=self.accounts is None or (user or {}).get("role") == "attorney",
                                present=lambda case: (self.data_root / case / "fact_graph.json").exists() or (self.data_root / case / "documents.json").exists(),
                                may_open=gate, wording_ok=wording_ok)
        except find.ModelUnavailable as exc:
            raise LookupError(f"{exc} Ask whoever runs the system to start it; Search above still finds words.") from None
        find.log_question(self.data_root, question, str(body.get("reviewer") or ""), role, len(found["results"]))
        for hit in found["results"]:  # each hit names its case as Search does: the person, the kind of case
            if hit["case"]:
                name, kind = self._name_and_kind(self.data_root / hit["case"])
                hit["name"], hit["case_kind"] = name or hit["case"], kind
        return found | {"built": found["built"] and ready}

    def eoir26a_paper(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """The respondent signed the form on paper: the scan and the day it was signed."""
        import base64
        import binascii

        import eoir26a

        try:
            data = base64.b64decode(str(body.get("data") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("That file didn't arrive whole: add it again.") from None
        with self._lock:
            out = eoir26a.paper(self._engagement_dir(client_id), data, str(body.get("on") or ""), str(body.get("reviewer") or ""), role, self.portal_root)
        self._requery(self._engagement_dir(client_id))
        return out

    def eoir26a_pdf(self, client_id: str, paper_copy: bool = False) -> Path:
        """The form as it stands now (filled again from the case first), or the paper scan the client signed."""
        import eoir26a

        d = self._engagement_dir(client_id)
        if paper_copy:
            name = ((eoir26a.read(d).get("request") or {}).get("signature") or {}).get("file")
            path = d / eoir26a.FOLDER / str(name or "")
            if not name or not path.is_file():
                raise LookupError("There is no paper copy of this form.")
            return path
        with self._lock:
            return eoir26a.pdf_path(d, portal_root=self.portal_root)

    def case_file(self, client_id: str, user: dict | None) -> Path:
        """The client's file made to hand over (the attorney's)."""
        import engagement

        self._attorneys_only(user, "The client's file to hand over")
        return engagement.file_path(self._engagement_dir(client_id), self.data_root, self.portal_root)

    def firm_documents(self, user: dict | None) -> dict:
        """Settings, Firm documents: every office's letters, the words they may hold, and the attorney's approval of the wording."""
        import engagement

        return {"offices": engagement.documents(), "placeholders": [[k, v] for k, v in engagement.PLACEHOLDERS.items()],
                "languages": [[k, v] for k, v in engagement.LANGUAGE_NAMES.items()], "practice": engagement.practice(),
                "can_edit": self.accounts is None or (user or {}).get("role") == "attorney"}

    def firm_documents_save(self, body: dict, user: dict | None, role: str | None) -> dict:
        import engagement

        self._attorneys_only(user, "Changing the firm's letters")
        engagement.save_document(str(body.get("office") or ""), str(body.get("kind") or ""), body.get("texts") or {}, str(body.get("reviewer") or ""), role)
        return self.firm_documents(user)

    def retention_list(self, user: dict | None) -> dict:
        """Keeping current: the ended cases past their keeping date, and every destruction recorded (the attorney's)."""
        import engagement

        self._attorneys_only(user, "The files past their keeping date")
        return engagement.due(self.data_root)

    def retention_mark(self, body: dict, user: dict | None, role: str | None, *, session_token=None) -> dict:
        import engagement

        self._attorneys_only(user, "Recording a file as destroyed")
        with self._lock:
            who, case = str(body.get("reviewer") or ""), str(body.get("case") or "")
            if self.accounts is not None and session_token is not None:
                current_actor = self.accounts.session_user(session_token)
                if current_actor is None:
                    raise PermissionError("Sign in again before recording a destruction.")
                if not self.may_open(current_actor, case):
                    raise LookupError("unknown client")
                self._attorneys_only(current_actor, "Recording a file as destroyed")
                who, role = current_actor["name"], current_actor["role"]
                events.set_actor(who, role)
            return engagement.mark_destroyed(self.data_root, case, who, role,
                body.get("folder_removed") is True, body.get("export_kept") is True, str(body.get("note") or ""))

    def set_office(self, client_id: str, body: dict) -> dict:
        import offices

        with self._lock:
            offices.choose(self.client_dir(client_id), str(body.get("office") or ""), str(body.get("reviewer") or ""))
            self._requery(self.client_dir(client_id))
        return self.items(client_id)

    # -- the front desk: add a client, invite, add a scan, choose the questionnaire (review/front_desk.py) -----------

    def _portal(self):
        from portal.store import PortalStore

        if self.portal_root is None:
            raise ValueError("This installation has no client portal.")
        return PortalStore(self.portal_root)

    def front_desk(self) -> dict:
        """What the Add a client form offers (languages, offices, questionnaires, tracks)."""
        import offices
        from review import front_desk

        return front_desk.form_options(offices.offices())

    def client_add(self, body: dict, user: dict | None = None) -> dict:
        from review import front_desk

        import prospects

        with self._lock:
            from_call = None
            if body.get("prospect"):  # Add a client filled in from a first call: the prospect is opened through its gate first, and must still be open
                from_call = self.prospect_dir(user, str(body.get("prospect")))
                if self.portal_root is None:
                    raise ValueError("This installation has no client portal.")
                prospects.ensure_open(prospects.read(from_call))
            try:
                added = front_desk.add_client(self._portal(), self.data_root, body, str(body.get("reviewer") or ""), role=(user or {}).get("role"),
                                              may_see=self._may_see(user), carry_from=from_call, actor_email=(user or {}).get("email"))
            except OSError:
                if from_call is None:
                    raise
                self._family_actor(user)
                self.prospect_dir(user, from_call.name)  # current source ACL before exposing own recovery guidance
                raise case_assignment.Conflict("Client creation was not confirmed. Check this prospect's promotion recovery before creating another client.") from None
            if added.get("id"):
                self.roster.touch(str(added["id"]))  # the folder and the portal's profile exist now: the lists read them before the answer goes out
            if added.get("declined"):  # the conflict search's decision: not added (the decision is in the conflict log)
                return added
            if from_call is not None and "from_call" not in added:  # a reviewed new-target wrapper already completes promotion once
                added["from_call"] = prospects.became_client(self.data_root, self.portal_root, from_call.name, added["id"], self._portal(), str(body.get("reviewer") or ""),
                                                             (user or {}).get("role"), actor_email=(user or {}).get("email"))
            # a VAWA, T, U or asylum client is restricted already, and a client whose conflict check waits for an attorney is held: "Send the
            # invitation now" sends nothing (the screen says why)
            sent = front_desk.invite(self._portal(), added["id"], str(body.get("reviewer") or ""), cases_root=self.data_root) \
                if body.get("invite") and not added.get("restricted") and not added.get("held") else None
        self.roster.touch(added["id"])
        return added | ({"delivery": sent["delivery"]} if sent else {})

    # -- the conflict search (src/conflicts.py) -------------------------------------------------------------------------------------

    def _may_see(self, user: dict | None):
        """The gate for a hit: None (everything) for an attorney or without staff accounts; else may_open, as for every case route."""
        if self.accounts is None or user is None or user.get("role") == "attorney":
            return None
        return lambda case: self._listed_to(user, case)

    def conflict_search(self, body: dict, user: dict | None) -> dict:
        """The search, logged: for Add a client (attorney and paralegal), by hand (Settings: the attorney's), or again for a case waiting for a
        decision (the attorney's, for a case they may open). What comes back is what this person may see (conflicts.present)."""
        import conflicts

        purpose = str(body.get("purpose") or "add")
        role = (user or {}).get("role") if self.accounts is not None else None
        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: every conflict search records who ran it.")
        if purpose == "case":
            case = str(body.get("case") or "")
            if self.accounts is not None and (role != "attorney" or not self.may_open(user, case)):
                raise PermissionError("Running the search again for a case is the attorney's.")
            if (self._case_folder(case) or self._held_folder(case)) is None:
                raise LookupError("unknown client")
            record = conflicts.search_again(self.data_root / case, by=who, role=role)
        else:
            if purpose not in ("add", "hand"):
                raise ValueError("Choose why you are searching.")
            if purpose == "hand":
                self._attorneys_only(user, "The conflict search by hand")
            record = conflicts.search(self.data_root, body, by=who, role=role, purpose=purpose)
        return conflicts.present(record, self._may_see(user))

    def conflict_note(self, body: dict, user: dict | None) -> dict:
        """Settings, Conflict search: what the attorney decided about a search by hand."""
        import conflicts

        self._attorneys_only(user, "The conflict search by hand")
        return conflicts.note(self.data_root, str(body.get("search") or ""), str(body.get("decision") or ""), str(body.get("reason") or ""),
                              by=str(body.get("reviewer") or ""), role=(user or {}).get("role") if self.accounts is not None else None)

    def conflict_decide(self, client_id: str, body: dict, user: dict | None) -> dict:
        """An attorney's decision for a case whose conflict check waits (an import, a sync, or a client added as not yet decided)."""
        import conflicts

        if self.accounts is not None and (user or {}).get("role") != "attorney":
            raise PermissionError("Only an attorney records what a hit means.")
        folder = self._case_folder(client_id) or self._held_folder(client_id)
        if folder is None:
            raise LookupError("unknown client")
        try:
            with self._lock:
                return conflicts.decide(folder, str(body.get("decision") or ""), str(body.get("reason") or ""),
                                        by=str(body.get("reviewer") or ""), role=(user or {}).get("role") if self.accounts is not None else None)
        finally:
            self.roster.touch(client_id)  # the decision changes who is held: the lists read the case again before the answer goes out

    def conflict_checks(self, q: dict, user: dict | None, csv: bool = False):
        """Settings, "Conflict checks": every search and decision, 50 a page, filtered by person, kind and dates, with the cases waiting for an
        attorney's decision on the first page; or all of it as a CSV file. The attorney's (it holds the hits in full)."""
        import conflicts

        self._attorneys_only(user, "The list of conflict checks")
        if csv:
            return conflicts.csv(self.data_root, q)
        out = conflicts.listing(self.data_root, q, self._page_of(q))
        return out | {"waiting": conflicts.waiting(self.data_root, portal=self.portal_root)}

    def client_invite(self, client_id: str, body: dict) -> dict:
        from review import front_desk

        try:
            with self._lock:
                return front_desk.invite(self._portal(), client_id, str(body.get("reviewer") or ""), again=bool(body.get("again")), cases_root=self.data_root)
        finally:
            self.roster.touch(client_id)  # the invitation changes the client's row: the lists read it again before the answer goes out

    def contact_recovery(self, body: dict, user: dict | None, *, change=False) -> dict:
        """Own retained operation only, including interrupted missing profiles."""
        from portal import communication_consent as consent, contact_transitions
        from portal.store import PortalStore
        if self.accounts is None or not isinstance(user, dict) or not user.get("email"):
            raise PermissionError("Sign in with a current staff account first.")
        self._communication_body(body)
        prospect = body.get("store_kind", "client") == "prospect"
        if body.get("store_kind", "client") not in ("client", "prospect"):
            raise ValueError("Choose the client or prospect store.")
        portal, cases = self._need_portal().absolute(), self.data_root.absolute()
        scope = consent.Scope(cases.parent.parent, portal / "prospects" if prospect else portal,
                              cases.parent / "prospects" if prospect else cases)
        if self.accounts.path.absolute() != scope.data / "review_users.json":
            raise ValueError("Use this installation's configured staff account store.")
        client = body.get("client")
        store = __import__("prospects").store(portal) if prospect else PortalStore(portal)
        with consent.gate(scope):
            scope.check(store, client)
            case = scope.cases / client
            actor = consent.staff(scope, user["email"])
            if not case.is_dir() or not restricted.visible_to(actor, case):
                raise LookupError("unknown client")
            if prospect:
                self.prospect_dir(actor, client)
            # Helpers verify the intact retained coordinator and current lifecycle.
            if not change:
                try:
                    return contact_transitions.enrollment_recovery_view(scope, store, client, actor_email=actor["email"])
                except ValueError:
                    return contact_transitions.recovery_view(scope, store, client, actor_email=actor["email"])
            if body.get("action") == "recover_enrollment":
                return contact_transitions.recover_enrollment(scope, store, client, actor_email=actor["email"])
            if body.get("action") == "recover_contact":
                return contact_transitions.recover_transition(scope, store, client, actor_email=actor["email"])
        raise ValueError("Choose explicit same-ID enrollment or contact recovery.")

    def _communication(self, client_id: str, user: dict | None, *, prospect=False, recovery=False):
        """Trusted exact installation/store composition; recheck current authority."""
        from portal import communication_consent as consent
        from portal.store import PortalStore
        if self.accounts is None or not isinstance(user, dict) or not user.get("email"):
            raise PermissionError("Sign in with a current staff account first.")
        portal = self._need_portal().absolute()
        cases = self.data_root.absolute()
        root = cases.parent.parent
        scope = consent.Scope(root, portal / "prospects" if prospect else portal,
                              cases.parent / "prospects" if prospect else cases)
        if self.accounts.path.absolute() != scope.data / "review_users.json":
            raise ValueError("Use this installation's configured staff account store.")
        store = __import__("prospects").store(portal) if prospect else PortalStore(scope.portal)
        with consent.gate(scope):
            if prospect:
                self.prospect_dir(user, client_id)
            elif not self.may_open(user, client_id):
                raise LookupError("unknown client")
            case = scope.cases / client_id
            consent.staff(scope, user["email"], case=case)
            if not recovery:
                consent._open(scope, store, client_id)
        return scope, store, user["email"]

    def communication_service(self):
        """Compose authenticated staff consent from this installation's authority."""
        from law_app.adapters.identity.local_accounts import LocalAccountsIdentity
        from law_app.adapters.persistence.filesystem.case_access import CanonicalCaseAccess
        from law_app.domain.cases.access_policy import CaseAccessPolicy
        from law_app.application.communications.consent import ConsentWorkflow
        return ConsentWorkflow(
            LocalAccountsIdentity(self.accounts, self.data_root.absolute().parent / 'review_users.json'),
            CaseAccessPolicy(CanonicalCaseAccess(self)), view=self.communication_view,
            change=self.communication_change, validate_body=self._communication_body)

    def authenticated_communication_view(self, client_id, session_token, prospect=False):
        return self.communication_service().status(session_token, client_id, prospect=prospect)

    def authenticated_communication_change(self, client_id, body, session_token, *, prospect=False):
        return self.communication_service().change(session_token, client_id, body, prospect=prospect)

    def promotion_recovery(self, body: dict, user: dict | None, *, change=False) -> dict:
        from portal import communication_consent as consent, promotion
        from portal.store import PortalStore
        self._communication_body(body)
        if not isinstance(body.get("store_kind", "prospect"), str) or body.get("store_kind", "prospect") not in ("client", "prospect"):
            raise ValueError("Choose the client or prospect store.")
        current = self._family_actor(user)
        prospect = body.get("store_kind", "prospect") == "prospect"
        portal, cases = self._need_portal().absolute(), self.data_root.absolute()
        scope = consent.Scope(cases.parent.parent, portal / "prospects" if prospect else portal, cases.parent / "prospects" if prospect else cases)
        if self.accounts.path.absolute() != scope.data / "review_users.json":
            raise ValueError("Use this installation's configured staff account store.")
        store = __import__("prospects").store(portal) if prospect else PortalStore(portal)
        client = body.get("client")
        with consent.gate(scope):
            scope.check(store, client)
            case = scope.cases / client
            if not case.is_dir() or not restricted.visible_to(current, case):
                raise LookupError("unknown client")
            consent.staff(scope, current["email"], case=case)
            try:
                if change:
                    operation = body.get("operation")
                    if body.get("action") != "recover" or not isinstance(operation, str) or not re.fullmatch(r"[0-9a-f]{32}", operation):
                        raise ValueError("Choose the exact retained promotion operation.")
                    promotion.recover(scope, store, client, operation, actor_email=current["email"])
                return promotion.recovery_view(scope, store, client, actor_email=current["email"])
            except PermissionError:
                self._family_actor(user)  # current account failures remain 403; a hidden peer is unknown
                raise LookupError("unknown client") from None

    @staticmethod
    def _evidence_bytes(body: dict) -> bytes:
        content = body.get("content_base64")
        if not isinstance(content, str) or len(content) > (4 * 1024 * 1024 + 2) // 3 * 4:
            raise ValueError("Supply nonempty evidence of at most 4 MiB.")
        try:
            return base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Evidence must use valid base64 encoding.") from None

    @read_scope.scoped
    def communication_view(self, client_id: str, user: dict | None, prospect=False) -> dict:
        from portal import communication_consent as consent, contact_transitions, contact_access, contact_control
        import client_language_readiness
        scope, store, actor = self._communication(client_id, user, prospect=prospect, recovery=True)
        with consent.gate(scope), read_scope.contacts():
            consent.staff(scope, actor, case=scope.cases / client_id)
            try:
                profile = store.profile(client_id)
            except LookupError:
                if prospect:
                    profile = __import__("prospects").read(self.prospect_dir(user, client_id))
                else:
                    profile = {}
            phone_view = contact_access.enrollment_contacts(None, profile.get("email") or "", profile.get("phone") or "")
            try:
                control_view = contact_control.staff_view(scope, store, client_id, actor_email=actor)
            except (ValueError, LookupError):
                control_view = {channel: {"verified": False, "reason": "Current enrollment or contact control is unavailable."}
                                for channel in ("email", "sms", "whatsapp")}
            if profile.get("phone"):
                try:
                    contact_access.normalize_phone(profile["phone"])
                except ValueError:
                    pass  # local numbers remain office contact only
                else:
                    safe_phone = {channel: contact_access.contact_eligibility(scope, store, client_id, channel, purpose="verify_contact")
                                  for channel in ("sms", "whatsapp")}
                    if any(row["eligible"] for row in safe_phone.values()):
                        verified = [channel for channel in ("sms", "whatsapp") if control_view[channel]["verified"] and safe_phone[channel]["eligible"]]
                        phone_view = {"phone_access": "verified" if verified else "verification_required",
                                      "phone_note": ("Current " + ", ".join(verified) + " control is verified. Separate current channel consent still applies.") if verified
                                      else "Text and WhatsApp access require actual current channel approval and explicit neutral verification. Dispatch alone does not verify control; the client must redeem the verification link."}
                    else:
                        phone_view["phone_note"] = "Office/manual follow-up for this phone. A safe unique contact and actual current channel approval are required before neutral verification."
            recovery_views = {}
            for name, helper in (("contact_recovery", contact_transitions.recovery_view),
                                 ("enrollment_recovery", contact_transitions.enrollment_recovery_view)):
                try:
                    recovery_views[name] = helper(scope, store, client_id, actor_email=actor)
                except (ValueError, LookupError) as exc:
                    recovery_views[name] = {"state": "unavailable", "reason": str(exc)}
            channels = {channel: {key: value for key, value in consent.eligibility(scope, store, client_id, channel).items()
                                  if key in ("allowed", "reason")} for channel in sorted(consent.CHANNELS)}
            from portal import staff_access
            preferences = staff_access.permission_view(scope, store, client_id, actor_email=actor) if profile.get("id") == client_id else {"revision": 0, "channels": [], "note": ""}
            from review.state import display_name
            current_name = profile.get("name") or profile.get("display_name") or "New case"
            if not prospect:
                current_name = display_name(scope.cases / client_id, current_name)
            return {"channels": channels, "preferences": preferences, "can_send": any(row.get("allowed") for row in channels.values()),
                    "identity": {"id": client_id, "name": current_name, "kind": "prospect" if prospect else "client"},
                    "language": profile.get("language"), "contacts": {key: profile.get(key, "") for key in ("email", "phone")},
                    "phone_access": phone_view.get("phone_access"), "phone_note": phone_view.get("phone_note"), "readiness": client_language_readiness.readiness(scope, profile.get("language")),
                    "manual_access": "consent_only", "email_control": control_view["email"]["verified"], "contact_control": control_view,
                    **recovery_views}

    @staticmethod
    def _communication_body(body: dict) -> None:
        for name in ("action", "language", "mode", "request", "channel", "kind", "format", "reviewer_name", "qualification", "fallback_reason", "notice_version"):
            if name in body and not isinstance(body[name], str):
                raise ValueError("Supply valid text for " + name.replace("_", " ") + ".")
        if "evidence" in body:
            ref = body["evidence"]
            if (not isinstance(ref, dict) or set(ref) != {"kind", "sha256", "format"}
                    or any(not isinstance(ref.get(name), str) for name in ("kind", "sha256", "format"))):
                raise ValueError("Choose a valid internal evidence kind, digest and format.")

    def communication_change(self, client_id: str, body: dict, user: dict | None, *, prospect=False) -> dict:
        from portal import communication_consent as consent, request_readiness, contact_transitions
        self._communication_body(body)
        action = body.get("action")
        scope, store, actor = self._communication(client_id, user, prospect=prospect,
                                                 recovery=action in ("prepare", "revoke", "evidence_read", "recover_contact", "recover_enrollment") or prospect and action in ("record_permission", "questionnaire_link"))
        if action in ("record_permission", "questionnaire_link"):
            from portal import staff_access
            if prospect:
                import prospects
                with consent.gate(scope):
                    actor_row = consent.staff(scope, actor, case=scope.cases / client_id)
                    rec = prospects.read(self.prospect_dir(user, client_id))
                    prospects.ensure_open(rec)
                    prospects.ensure_portal(store, rec, actor_row.get("name") or actor)
            if action == "record_permission":
                return staff_access.record_permission(scope, store, client_id, actor_email=actor,
                    channels=body.get("channels"), agreed=body.get("agreed"), note=body.get("note", ""),
                    expected_revision=body.get("expected_revision", 0))
            link = staff_access.issue(scope, store, client_id, actor_email=actor,
                agreed=body.get("agreed"), note=body.get("note", ""))
            token = link.pop("token")
            link["url"] = os.environ.get("PORTAL_BASE_URL", "http://localhost:8600").rstrip("/") + "/l/" + token
            return {"link": link, "notified": False}
        if action == "verify_contact":
            if body.get("channel") not in ("sms", "whatsapp"):
                raise ValueError("Choose text message or WhatsApp for neutral phone verification.")
            from portal.notify import Notifier
            verification = Notifier(store.root / "outbox.jsonl", store=store, cases_root=scope.cases).verify_contact(
                store.profile(client_id), body["channel"], actor_email=actor)
            return {"verification": verification, "questionnaire_access_granted": False}
        if action == "prepare" and prospect:
            import prospects
            with consent.gate(scope):
                actor_row = consent.staff(scope, actor, case=scope.cases / client_id)
                rec = prospects.read(self.prospect_dir(user, client_id))
                prospects.ensure_open(rec)
                prospects.ensure_portal(store, rec, actor_row.get("name") or actor)
            return {"prepared": True, "notified": False}
        if action == "evidence":
            return {"evidence": consent.retain_evidence(scope, self._evidence_bytes(body), actor_email=actor,
                    kind=body.get("kind"), format=body.get("format", "json"), store=store, client=client_id)}
        if action == "evidence_read":
            with consent.gate(scope):
                consent.staff(scope, actor, case=scope.cases / client_id)
                content = consent.evidence(scope, body.get("evidence"), client=client_id)
            return {"content_base64": base64.b64encode(content).decode("ascii"), "evidence": body.get("evidence")}
        if action == "grant":
            if not isinstance(body.get("evidence"), dict) or body["evidence"].get("kind") != "consent":
                raise ValueError("Actual own-client consent evidence is required.")
            for name in ("channel", "client_approved_at", "notice_version", "language", "source_kind", "approval_description"):
                if not isinstance(body.get(name), str):
                    raise ValueError("Supply the actual approval channel, time, wording, language, source and description.")
            return {"grant": consent.grant(scope, store, client_id, body.get("channel"), actor_email=actor,
                    evidence_ref=body.get("evidence"), client_approved_at=body.get("client_approved_at"),
                    notice_version=body.get("notice_version"), language=body.get("language"),
                    source_kind=body.get("source_kind"), approval_description=body.get("approval_description"))}
        if action in ("contact", "language"):
            if action == "contact":
                contacts = body.get("contacts")
                if (not isinstance(contacts, dict) or set(contacts) != {"email", "phone"}
                        or any(not isinstance(value, str) or len(value) > 320 for value in contacts.values())):
                    raise ValueError("Supply only the current email and phone contact fields.")
                changes = contacts
            else:
                import client_language_readiness
                if body.get("language") not in client_language_readiness.LANGUAGES:
                    raise ValueError("Choose a supported current client language.")
                changes = {"language": body["language"]}
            with consent.gate(scope):
                _, _, case = consent._open(scope, store, client_id)
                current_actor = consent.staff(scope, actor, case=case)
                try:
                    store.update_profile(client_id, **changes)
                except (OSError, TimeoutError):
                    raise ValueError("The change was not confirmed. Inspect the recorded same-ID contact recovery before trying again; do not create another client.") from None
                kind, case_id, home = store._ledger_where(client_id, "portal")
                audited = events.record(kind, "staff_contact_changed" if action == "contact" else "staff_language_changed",
                    "Staff changed current contact fields." if action == "contact" else "Staff changed the client language; fresh signoff and access are required.",
                    case=case_id, home=home, who=actor, role=current_actor["role"])
                return {"saved": True, "notified": False, "reconsent_required": True,
                        "audit_status": "recorded" if audited else "unavailable"}
        if action == "revoke":
            channels = body.get("channels")
            if not isinstance(channels, list) or any(not isinstance(channel, str) for channel in channels):
                raise ValueError("Choose explicit communication channels to revoke.")
            return consent.revoke(scope, store, client_id, channels, actor_email=actor)
        if action == "request_review":
            if not isinstance(body.get("publish", False), bool):
                raise ValueError("Choose whether to publish the reviewed request.")
            return request_readiness.review_request(scope, store, client_id, body.get("request"), actor_email=actor,
                    evidence_ref=body.get("evidence"), mode=body.get("mode"), reviewer_name=body.get("reviewer_name"),
                    qualification=body.get("qualification"), fallback_reason=body.get("fallback_reason"), publish=body.get("publish", False))
        if action == "recover_contact":
            return contact_transitions.recover_transition(scope, store, client_id, actor_email=actor)
        if action == "recover_enrollment":
            return contact_transitions.recover_enrollment(scope, store, client_id, actor_email=actor)
        raise ValueError("Choose an explicit consent, evidence, wording or recovery action.")

    def client_wording(self, body: dict, user: dict | None, *, change=False) -> dict:
        self._communication_body(body)
        from portal import communication_consent as consent
        import client_language_readiness
        if self.accounts is None or not isinstance(user, dict) or not user.get("email"):
            raise PermissionError("Sign in with a current staff account first.")
        scope = consent.Scope(self.data_root.absolute().parent.parent, self._need_portal().absolute(), self.data_root.absolute())
        if self.accounts.path.absolute() != scope.data / "review_users.json":
            raise ValueError("Use this installation's configured staff account store.")
        actor, language = user["email"], body.get("language", "en")
        with consent.gate(scope):
            consent.staff(scope, actor, attorney=True)
            if not change:
                return client_language_readiness.readiness(scope, language, notice_version=body.get("notice_version"))
            if body.get("action") == "evidence":
                return {"evidence": consent.retain_evidence(scope, self._evidence_bytes(body), actor_email=actor,
                        kind="wording", format=body.get("format", "json"))}
            if body.get("action") == "evidence_read":
                content = consent.evidence(scope, body.get("evidence"))
                return {"content_base64": base64.b64encode(content).decode("ascii"), "evidence": body.get("evidence")}
            if body.get("action") == "attorney_review":
                return client_language_readiness.review_attorney(scope, language, actor_email=actor, evidence_ref=body.get("evidence"))
            if body.get("action") == "translation_review":
                return client_language_readiness.review_translation(scope, language, actor_email=actor,
                        reviewer_name=body.get("reviewer_name"), qualification=body.get("qualification"), evidence_ref=body.get("evidence"))
        raise ValueError("Choose an actual wording evidence or review action.")

    def client_link(self, client_id: str, body: dict, role: str | None, *, user: dict | None = None) -> dict:
        """An attorney hands over assisted consent-only access, never email control."""
        from review import front_desk

        if role == "paralegal":
            raise PermissionError("Showing a client's sign-in link is the attorney's. Send the invitation instead: it goes to the client by the channels they agreed to.")
        with self._lock:
            return front_desk.show_link(self._portal(), client_id, str(body.get("reviewer") or ""), os.environ.get("PORTAL_BASE_URL", "http://localhost:8600"),
                                       cases_root=self.data_root, actor_email=(user or {}).get("email"))

    def client_questionnaire(self, client_id: str, body: dict) -> dict:
        from review import front_desk

        with self._lock:
            return front_desk.set_questionnaire(self._portal(), client_id, str(body.get("filing") or ""), str(body.get("reviewer") or ""),
                                                confirmed=bool(body.get("confirmed")))

    def _upload_actor(self, user: dict | None) -> str:
        if self.accounts is None or not isinstance(user, dict) or not isinstance(user.get("email"), str):
            raise PermissionError("Sign in with an active staff account before adding or recovering documents.")
        if self.accounts.path.absolute() != self.data_root.parent / "review_users.json":
            raise ValueError("Use this installation's configured data/review_users.json staff account store for document processing.")
        return user["email"]

    def _upload_actor_reader(self, email: str) -> dict | None:
        # The helper invokes this under its case lock; snapshots in the body or
        # session are not current account authority.
        return next((u for u in self.accounts.users() if u["email"] == email), None) if self.accounts is not None else None

    def _prepared_source_scope(self):
        from connectors.drive_intake import FirmScope
        self._staff_absolute_roots()
        scope = FirmScope(self.data_root.parent.parent)
        if (scope.cases != self.data_root or scope.queue != self.jobs_root
                or self.portal_root is None or Path(self.portal_root).absolute() != scope.portal):
            raise ValueError("Use this prepared installation's configured case, queue and main portal roots.")
        return scope

    def _staff_documents_root(self) -> Path:
        self._staff_absolute_roots()
        configured = os.environ.get("I485_CLIENTS_ROOT")
        # This is trusted process configuration; the helper validates its
        # reparse/ownership boundaries. No request can choose a source root.
        return Path(configured) if configured else self._prepared_source_scope().documents

    def _staff_absolute_roots(self) -> None:
        configured = os.environ.get("I485_CLIENTS_ROOT")
        if (not self.jobs_root.is_absolute() or (configured and not Path(configured).is_absolute())
                or (self.portal_root is not None and not Path(self.portal_root).is_absolute())):
            raise ValueError("Configure absolute document, job and portal roots before adding documents.")

    def _wake_upload_worker(self, clients: Path, portal: Path | None, queue: Path) -> bool:
        if Path(clients) != self.data_root or Path(queue) != self.jobs_root or jobs.folder_for(clients) != self.jobs_root:
            raise ValueError("The upload worker roots no longer match this installation.")
        expected_portal = Path(self.portal_root).absolute() if self.portal_root is not None else None
        if (Path(portal).absolute() if portal is not None else None) != expected_portal:
            raise ValueError("The upload worker portal does not match this installation.")
        return jobs.ensure_worker(clients, expected_portal)

    def staff_upload_outcome(self, client_id: str, attempt: str, user: dict | None) -> dict:
        from review import front_desk
        actor = self._upload_actor(user)
        documents_root = self._staff_documents_root()
        return front_desk.staff_upload_outcome(self.data_root, self._portal() if self.portal_root is not None else None,
            client_id, attempt, actor_email=actor, jobs_root=self.jobs_root,
            documents_root=documents_root, actor_reader=self._upload_actor_reader,
            may_access=restricted.visible_to, case_timeout=0)

    def source_setup(self, client_id: str, user: dict | None) -> dict:
        import source_association
        actor = self._upload_actor(user)
        scope = self._prepared_source_scope()
        return source_association.associate(scope.root, self._portal(), client_id, actor_email=actor,
            actor_reader=self._upload_actor_reader, may_access=restricted.visible_to, use_policies=True)

    def client_upload(self, client_id: str, body: dict, user: dict | None = None) -> dict:
        """One authenticated same-file attempt; receipt/job outcomes are distinct
        from processing, source approval and packet readiness."""
        import base64
        import binascii

        from review import front_desk

        actor = self._upload_actor(user)
        documents_root = self._staff_documents_root()
        if not isinstance(body.get("data"), str) or not isinstance(body.get("name"), str):
            raise ValueError("Choose a complete file with its original name.")
        try:
            data = base64.b64decode(body["data"], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("That file didn't arrive whole: add it again.") from None
        return front_desk.accept_staff_upload(self.data_root, self._portal() if self.portal_root is not None else None,
            client_id, body["name"], data, body.get("attempt"), actor_email=actor, jobs_root=self.jobs_root,
            documents_root=documents_root, wake_worker=self._wake_upload_worker,
            actor_reader=self._upload_actor_reader, may_access=restricted.visible_to, case_timeout=0)

    def warm_up(self) -> None:
        """Reads what the first request of each list would have read: every case's row (or the saved copy of them), and the staff log's, the view log's and the
        ledger's indexes. A person who opens a list after the app starts finds it ready. Never raises: a list reads for itself if this did not."""
        try:
            if self.scaled:
                self.roster.sync()
            self.views.index.refresh()
            if self.staff_log is not None:
                self.staff_log.page()
            self.events.firm()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"warm-up stopped ({type(exc).__name__})\n")

    def _sweep(self) -> None:
        """Readings a stopped worker left are marked "Did not finish: will be read tonight"; each such case is read again by the lists."""
        for case in jobs.sweep(self.data_root, self.portal_root):
            self.roster.touch(case)

    def _job_started(self, job: dict) -> dict:
        """What a request that wrote a job down answers: the job in words. A laptop with only the app has no worker of its own, so one is started (src/jobs.py)."""
        jobs.ensure_worker(self.data_root, self.portal_root)
        return {"job": jobs.view(job)}

    def jobs_of(self, client_id: str) -> dict:
        """The readings under way, waiting or done in the last few minutes on this case (the route is gated by may_open): what the Documents tab shows."""
        self._sweep()  # a worker that stopped leaves its job "Did not finish: will be read tonight"
        return {"jobs": [jobs.view(j) for j in jobs.jobs(self.jobs_root, client=client_id)], "worker": jobs.alive(self.jobs_root)}

    def jobs_firm(self, user: dict | None, q: dict) -> dict:
        """The readings that belong to no case (the notice inbox), and how many are waiting or running across the firm: no case is named. ?id= one job, by its
        id: its answer ("Placed on ...") only to the person who started it, and a job on a case only to someone who may open that case."""
        self._sweep()
        if q.get("id"):
            job = jobs.get(self.jobs_root, str(q["id"]))
            if job is None or (job.get("client") and not self.may_open(user, job["client"])):
                raise LookupError("unknown job")
            mine = job.get("by") == ((user or {}).get("name") or job.get("by"))
            return {"job": jobs.view(job, with_result=mine)}
        return {"counts": jobs.counts(self.jobs_root), "worker": jobs.alive(self.jobs_root),
                "inbox": [jobs.view(j) for j in jobs.jobs(self.jobs_root) if not j.get("client") and j.get("kind") != "restore_drill"]}

    def _policy_labels(self):
        from review.state import concise

        return {"input_for": self.catalog.input, "label_for": lambda key: concise(self.catalog.label(key), 120)}

    def policies(self, role: str | None) -> dict:
        """The firm's standard answers as Settings shows them: each one's words, its answers, whether it applies, and who last changed it (src/rules/firm_policies.py)."""
        from review.state import rule_info, source_text
        from rules import firm_policies

        rows = firm_policies.listing(**self._policy_labels())
        for row in rows:
            info = rule_info("POLICY:" + row["id"])
            row["approval"], row["approval_text"] = info["approval"]["state"], info["approval_text"]
            row["source"] = source_text(row["source"])  # the words the screen uses for where a policy comes from (no file names)
        if role != "paralegal":  # every edit of each policy, newest first: the attorney's record (src/rules/firm_policies.py changes)
            by_policy: dict[str, list] = {}
            for change in firm_policies.changes(self._policy_labels()["label_for"]):
                by_policy.setdefault(change["policy"], []).append(change)
            for row in rows:
                row["edits"] = by_policy.get(row["id"], [])
        return {"policies": rows, "can_edit": role != "paralegal"}

    def policies_edit(self, body: dict, role: str | None) -> dict:
        """The attorney changes a firm policy's words, its answers or whether it applies; recorded like an approval (who, when, before and after)."""
        from rules import firm_policies

        if role == "paralegal":
            raise PermissionError("The firm's standard answers are the attorney's to change. You can read them here.")
        with self._lock:
            if body.get("revert"):  # "Back to the shipped wording": recorded like an edit, the history keeps what was undone
                firm_policies.revert(str(body.get("policy") or ""), str(body.get("reviewer") or ""), role)
            else:
                firm_policies.edit(str(body.get("policy") or ""), str(body.get("reviewer") or ""), role,
                                   plain_text=body["plain_text"] if "plain_text" in body else None, answers=body.get("answers") or None,
                                   enabled_=body["enabled"] if "enabled" in body else None, **self._policy_labels())
        return self.policies(role)

    def decide(self, client_id: str, body: dict, role: str | None = None) -> dict:
        """role: the signed-in person's (None when there are no accounts)."""
        from review.state import concise, prepare_decision, record_prepared_decision

        d = self.client_dir(client_id)
        with self._lock, jobs.case_lock(jobs.folder_for(d.parent), d.name):
            current = {i["id"]: i for i in build_items(d, self.field_map, self.template, self.catalog, pending=False)["open"]}
            # "decisions": [{item_id, action, values}, ...] shares reviewer/note --
            # one card can confirm some facts and correct others in one step.
            batch = body.get("decisions") or [{"item_id": iid, "action": body.get("action"), "values": body.get("values")}
                                               for iid in (body.get("item_ids") or [body.get("item_id")])]
            for one in batch:  # validate everything before recording anything
                if one["item_id"] not in current:
                    raise ValueError(f"item {one['item_id']} is no longer open: reload")
                if role == "paralegal" and needs_attorney(current[one["item_id"]], one.get("action")):
                    raise PermissionError("This needs an attorney's sign-off. You can read it; an attorney decides it.")
            prepared = [(current[one["item_id"]], prepare_decision(d, current[one["item_id"]],
                         body | one | {"role": role})) for one in batch]
            recorded = [record_prepared_decision(d, item, prepared_decision) for item, prepared_decision in prepared]
            # Settling an answer can unlock the answers a firm policy or a rule fills in from it (the SSN settled: the Social
            # Security card items follow). They did not exist before, so the open count can rise: name them, never leave a bare surprise.
            unlocked = [{"id": i["id"], "title": concise(", ".join(dict.fromkeys(f["short"] for f in i["facts"])) or i["title"], 80)}
                        for i in build_items(d, self.field_map, self.template, self.catalog, pending=False)["open"] if i["id"] not in current]
            skipped = self._skip_portal_tasks(client_id, d)
            self._requery(d)
            return {"recorded": len(recorded), "unlocked": unlocked, "client_not_asked": len(skipped)}

    def _skip_portal_tasks(self, client_id: str, client_dir: Path) -> list:
        """The client's portal list loses what the office just decided (portal/engine.py skip_decided)."""
        if self.portal_root is None or not (self.portal_root / "clients" / client_id / "profile.json").exists():
            return []
        from portal.engine import decided_facts, settle_decided, skip_decided
        from portal.store import PortalStore

        store, decided = PortalStore(self.portal_root), decided_facts(client_dir)
        settle_decided(store, client_id, decided)  # their answers on this card are dealt with: the portal's "sent to the office" line goes
        return skip_decided(store, client_id, decided)

    def undo(self, client_id: str, body: dict, role: str | None = None) -> dict:
        d = self.client_dir(client_id)
        who = str(body.get("reviewer") or "").strip()
        if not who:
            raise ValueError("Enter your name first: an undo records who reopened the item.")
        with self._lock:
            decision = load_decisions(d).get(body["item_id"])
            if role == "paralegal" and decision and needs_attorney(decision.get("item", {}), decision.get("action")):
                raise PermissionError("Only an attorney can reopen an attorney's sign-off.")
            undo_decision(d, body["item_id"], who, role)  # marked undone, kept in the Decision log's history
            import part14_explain

            part14_explain.after_undo(d, str(body["item_id"]), who, role)  # an explanation's approval reopened here is taken back from the firm's wording too
            self._requery(d)
        return {"ok": True}

    def apply(self, client_id: str) -> dict:
        with self._lock:
            done = refill(self.client_dir(client_id), self.field_map, self.template)
            events.record("packet", "refilled", "Filled the forms again from the decisions made so far", case_dir=self.client_dir(client_id))
            return done


def make_handler(app: ReviewApp, port: int, hostnames: tuple[str, ...] = ()):
    allowed_hosts = {f"{name}:{port}" for name in ("127.0.0.1", "localhost", *hostnames)} | set(hostnames)
    # reachable without signing in: the page itself, and signing in (with a password, or Microsoft or Google)
    open_paths = {"/", "/api/me", "/api/login", "/api/logout", "/api/password", "/api/code", "/api/enrol", "/auth/start", "/auth/callback", "/api/setup"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "I485Review"
        timeout = REQUEST_TIMEOUT  # socket timeout: a client that stops sending mid-request is dropped
        _touch_case: str | None = None  # the case a write is changing: the lists are told before the answer is sent, not after (the person's next look must see it)
        _touch_all = False
        _answered = False  # the answer's headers have gone out (a 500 after that would corrupt it)
        _support_view = None  # a masked support session's view (src/support.py): every answer passes through its mask; None for staff and a plain session
        _support_path = ""

        def log_message(self, fmt, *args):  # keep client data out of console logs
            path = urlparse(getattr(self, "path", "") or "").path  # a request line http.server refused (too long, malformed) has no path
            if any(path.startswith(p) for p in TOKENED):  # the address is the credential (TOKENED): the console never holds it
                path = "/calendar/<redacted>.ics"
            took = time.monotonic() - getattr(self, "_asked", time.monotonic())  # how long the answer took to start: the log shows a slow request by its seconds
            sys.stderr.write(f"{clock.stamp('seconds')[11:19]} {self.command} {path[:200]} -> {args[1] if len(args) > 1 else ''} {took:.2f}s\n")  # the firm's clock, not the computer's

        def parse_request(self):
            self._asked = time.monotonic()  # the request line has arrived (a keep-alive wait before it is not the request's time)
            return super().parse_request()

        # -- the proxy in front, if any ---------------------------------------------

        def _proxied(self) -> bool:
            """The request came from a proxy the operator named (--trusted-proxy): its X-Forwarded-* are believed."""
            return self.client_address[0] in app.trusted_proxies

        def _tls(self) -> bool:
            """The person's browser reached us over HTTPS (through the firm's TLS proxy)."""
            if getattr(self, "headers", None) is None:  # http.server refused the request before reading its headers
                return app.behind_tls
            forwarded = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
            return app.behind_tls or (self._proxied() and forwarded == "https")

        def _address(self) -> str:
            """The person's address: the trusted proxy's X-Forwarded-For (its first address), else the connection's."""
            forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            return forwarded if forwarded and self._proxied() else self.client_address[0]

        def _headers(self, content_type: str, nonce: str | None = None, cache: str = "no-store") -> None:
            """What every answer carries, whichever way it goes out (_send, _stream, _export_download, send_error): no caching (the calendar file:
            private, so no proxy keeps it), no type sniffing, no referrer, never in a frame, the Content-Security-Policy for its type (csp), no camera,
            microphone or location, its own window and its own site only (COOP, CORP), and HSTS behind the firm's TLS proxy."""
            self._answered = True
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", csp(content_type, nonce))
            self.send_header("Permissions-Policy", PERMISSIONS)
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if self._tls():
                self.send_header("Strict-Transport-Security", HSTS)

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | list | None = None, cache: str = "no-store"):
            if self._support_view is not None and not self._support_sendable(content_type):  # the boundary's last word: no file to a masked session
                body, content_type, extra, status = json.dumps({"error": support.REFUSED_FILE}).encode(), "application/json; charset=utf-8", None, 403
            if self._touch_case is not None and _plain_case(self._touch_case):
                app.roster.touch(self._touch_case)
            if self._touch_all:
                app.roster.touch_all()
            nonce = None
            if content_type.startswith("text/html"):  # a page: its script and style run with this answer's nonce only
                nonce = secrets.token_urlsafe(18)
                body = body.replace(NONCE, f'nonce="{nonce}"'.encode())
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._headers(content_type, nonce, cache)
            for k, v in (extra.items() if isinstance(extra, dict) else extra or ()):
                self.send_header(k, v)
            try:
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # the browser moved on before the answer was written (a page change mid-load): nothing to report
                self.close_connection = True

        def _export_download(self, q: dict, user: dict | None) -> None:
            """The finished export as a download, read from the disk a piece at a time (the zip can be many gigabytes: never held in memory)."""
            try:
                path = app.export_file(str(q.get("name") or ""), user)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            except LookupError as exc:
                return self._json({"error": str(exc)}, 404)
            if not self._viewed(user, "", "export", path.name):
                return
            if support.is_support(user):  # never to support, plain or masked: the export holds every case, the restricted ones too
                return self._json({"error": support.REFUSED_FILE}, 403)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self._headers("application/zip")
            self.end_headers()
            try:
                with open(path, "rb") as f:
                    for piece in iter(lambda: f.read(1 << 20), b""):
                        self.wfile.write(piece)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True

        def _stream(self, path: Path, name: str) -> None:
            """A file from the disk as a download, a piece at a time (never held in memory)."""
            if self._support_view is not None:
                return self._json({"error": support.REFUSED_FILE}, 403)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self._headers("application/zip")
            self.end_headers()
            try:
                with open(path, "rb") as f:
                    for piece in iter(lambda: f.read(1 << 20), b""):
                        self.wfile.write(piece)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True

        def send_error(self, code, message=None, explain=None):
            """http.server's own refusals (a request line too long, a method this app has no handler for, a malformed request): the same headers
            as every answer, and a word, never its HTML page."""
            try:
                phrase = HTTPStatus(code).phrase
            except ValueError:
                phrase = "error"
            self.close_connection = True
            self._send(code, phrase.lower().encode(), "text/plain; charset=utf-8")

        def _failed(self) -> None:
            """An error nobody expected while answering: the detail on the console, a 500 in words to the person (with every header), never a
            dropped connection. Nothing more is sent when the answer had already started."""
            import traceback

            traceback.print_exc()
            if not self._answered:
                self._json({"error": SERVER_ERROR.format(support=__import__("deployment").support())}, 500)
            else:
                self.close_connection = True

        def _json(self, data, status=200, extra: dict | None = None):
            if self._support_view is not None:  # a masked support session: deny by default (src/support.py View.mask)
                data = self._support_view.mask(support.Shown(data) if self._support_path in SUPPORT_SHOWN and isinstance(data, dict) else data)
            self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8", extra)

        def _guard(self) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._send(HTTPStatus.FORBIDDEN, b"forbidden host", "text/plain")
                return False
            if app.accounts is not None:
                app.accounts.from_address(self._address())  # every row the access log gets while serving this request says where it came from
            return True

        def _answer(self, make, *args, csv: str = "") -> None:
            """An oversight page (JSON) or its CSV file (csv: the file's name); a date or a page that isn't one is a 400 in words."""
            try:
                body = make(*args)
            except jobs.CaseBusy as exc:
                return self._json({"error": str(exc)}, 409)
            except case_assignment.Unavailable as exc:
                return self._json({"error": str(exc)}, 503)
            except drive_settings.Conflict as exc:
                return self._json({"error": str(exc), "conflict": True}, 409)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            if csv:
                return self._send(200, body, "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="{csv}"'})
            self._json(body)

        # -- who is asking ------------------------------------------------------

        def _token(self, name: str = COOKIE) -> str | None:
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie") or "")
            except Exception:  # noqa: BLE001 -- a malformed cookie is just no session
                return None
            return jar[name].value if name in jar else None

        def _user(self) -> dict | None:
            return app.accounts.session_user(self._token()) if app.accounts else None

        def _signed_in(self, path: str) -> tuple[bool, dict | None]:
            """(may proceed, user). Without accounts everyone may proceed."""
            if app.accounts is None or path in open_paths:
                return True, self._user()
            user = self._user()
            if user is None:
                self._json({"error": "Please sign in.", "sign_in": True}, 401)
                return False, None
            if user["must_change"]:
                self._json({"error": "Choose your own password first.", "sign_in": True}, 403)
                return False, None
            return True, user

        def _same_machine(self) -> bool:
            """The request comes from the computer that runs this app, itself: a loopback connection to a loopback name, with nothing
            in front of it. A proxy on the same computer connects from loopback too, so any sign of one (a proxy named to the app, an
            HTTPS-only app, a forwarding header, another Host name) means this is not the computer itself."""
            if not app.local_setup or app.trusted_proxies or app.behind_tls or app.secure_cookies:  # each is a sign of a proxy in front
                return False
            if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                return False
            if any(self.headers.get(h) for h in ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Forwarded-Server", "Forwarded", "Via",
                                                 "X-Real-IP", "X-Client-IP", "True-Client-IP", "CF-Connecting-IP")):
                return False
            return (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower() in ("127.0.0.1", "localhost", "::1")

        def _setup(self, body: dict):
            """The first attorney (review/auth.py create_first): refused unless the body carries a setup code that was printed and has not
            expired (the computer itself too). Every refusal, and any account existing, is the same 404 "not found"."""
            token, user = app.accounts.create_first(str(body.get("email") or ""), str(body.get("name") or ""), str(body.get("password") or ""),
                                                    str(body.get("code") or "") or None)
            self._json({"user": user}, extra=self._cookie(token))

        def _cookie(self, token: str, max_age: int | None = None) -> dict:
            """The session cookie. No Max-Age: it lasts while the browser is open, and the server ends the session
            after the Settings page's minutes without use (review/auth.session_idle). Secure over HTTPS."""
            secure = "; Secure" if app.secure_cookies or self._tls() else ""
            age = f"; Max-Age={max_age}" if max_age is not None else ""
            return {"Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict{age}{secure}"}

        def _sign_in_cookie(self, state: str, max_age: int) -> tuple[str, str]:
            secure = "; Secure" if app.secure_cookies or self._tls() else ""
            return ("Set-Cookie", f"{SIGN_IN_COOKIE}={state}; Path=/auth/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}")

        def _auth_start(self, q: dict) -> None:
            """"Sign in with Microsoft / Google": off to the provider, with a one-use state (also in a cookie) and nonce."""
            from connectors.signin import SignInError

            if app.limited("/auth/start", self._address()):
                return self._send(HTTPStatus.TOO_MANY_REQUESTS, TOO_MANY.encode(), "text/plain; charset=utf-8")
            if q.get("provider") == "clio":
                return self._clio_start()
            try:
                url, state = app.sign_in_start(str(q.get("provider") or ""))
            except (LookupError, SignInError):
                return self._send(404, b"not found", "text/plain")
            self._send(302, b"", "text/plain", [("Location", url), self._sign_in_cookie(state, SIGN_IN_WINDOW)])

        def _clio_start(self) -> None:
            """"Connect to Clio" (Settings, Connections): an attorney only, signed in; off to Clio's consent screen with a
            one-use state, tied to this browser by the same cookie as staff sign-in."""
            from connectors.clio import ClioError

            user = self._user()
            if app.accounts is not None and (user is None or user["must_change"] or user["role"] == "paralegal"):
                return self._send(403, b"forbidden", "text/plain")
            try:
                url, state = app.clio_connect_start(user["name"] if user else "the attorney on this computer")
            except ClioError:
                return self._send(302, b"", "text/plain", [("Location", "/?clio=notready#settings:connections")])
            self._send(302, b"", "text/plain", [("Location", url), self._sign_in_cookie(state, SIGN_IN_WINDOW)])

        def _auth_callback(self, q: dict) -> None:
            """The provider sends the person back here: signed in and on to the page, or back to the sign-in screen
            with a reason (SIGN_IN_MESSAGES). Clio's answer (connecting the firm's account) goes back to Settings."""
            from connectors.signin import SignInError

            state = str(q.get("state") or "")
            if app.sign_in_kind(state) == "clio":
                outcome = "expired"
                if self._token(SIGN_IN_COOKIE) == state:  # the browser that started it (no connection planted by a link)
                    outcome = app.clio_connect_finish(state, str(q.get("code") or ""), str(q.get("error") or "") or None)
                return self._send(302, b"", "text/plain", [self._sign_in_cookie("", 0), ("Location", f"/?clio={outcome}#settings:connections")])
            outcome, session = "expired", None
            if app.accounts is None:
                return self._send(404, b"not found", "text/plain")
            if q.get("error"):  # the person cancelled at the provider, or it refused
                app.sign_in_cancel(state)
                outcome = "cancelled"
            elif state and self._token(SIGN_IN_COOKIE) == state:  # the browser that started it (no sign-in planted by a link)
                try:
                    session = app.sign_in_finish(state, str(q.get("code") or ""))
                except LookupError:
                    outcome = "expired"
                except SignInError:
                    outcome = "failed"
                except ValueError:
                    outcome = "refused"
            headers = [self._sign_in_cookie("", 0)]
            if session:
                headers += [("Location", "/"), tuple(self._cookie(session[0]).items())[0]]
            else:
                headers.append(("Location", f"/?signin={outcome}"))
            self._send(302, b"", "text/plain", headers)

        def _viewed(self, user: dict | None, client: str, kind: str, name: str = "") -> bool:
            """Before the file goes out: a row in the view log, so a crash or a dropped connection while it is sent never loses who opened it (the
            answer is made first, so a refusal or a file that is not there writes no row). When the row cannot be written (a full disk), nothing is
            sent: the person is told (503) and the console says why. True when the answer may go out."""
            try:
                app.viewed(user, client, kind, name, self._address())
                return True
            except OSError as exc:
                sys.stderr.write(f"view log not written, so the file was not sent: {type(exc).__name__}\n")
                self._json({"error": VIEW_LOG_FAILED}, 503)
                return False

        def _calendar(self, path: str) -> bool:
            """The tokened feed (TOKENED): /calendar/<token>.ics, answered here before anyone is asked to sign in, because a calendar program has no
            session: the token in the address is the credential and opens one person's view only. Any other token is the one 404 every unknown
            case gets. True when the path was a calendar address."""
            if not any(path.startswith(p) for p in TOKENED):
                return False
            if app.accounts is not None and app.limited("/calendar/", self._address()):
                self.close_connection = True
                self._json({"error": TOO_MANY_REQUESTS}, 429)
                return True
            m = re.fullmatch(r"/calendar/([A-Za-z0-9_-]+)\.ics", path)
            host = self.headers.get("Host") or ""
            body = app.calendar_file(m[1], f"{'https' if self._tls() else 'http'}://{host}", self._address()) if m else None
            if body is None:
                self._json(UNKNOWN, 404)
            else:
                self._send(200, body, "text/calendar; charset=utf-8", {"Content-Disposition": 'inline; filename="deadlines.ics"'}, cache="private, no-cache")
            return True

        # -- support (src/support.py) -------------------------------------------------

        def _support_sendable(self, content_type: str) -> bool:
            """What a masked support session may be sent: JSON (masked on its way out, _json) and the page itself; never a file."""
            return content_type.startswith("application/json") or (content_type.startswith("text/html") and self._support_path == "/") \
                or content_type.startswith("text/plain")

        def _support_request(self, method: str, path: str, q: dict, user: dict) -> bool:
            """A request by support: the case numbers of a masked session taken back to the case ids, the request in the ledger and the access log
            (before anything is answered), and refused when it writes (every POST but signing out) or asks for a file in a masked session. True
            when the route may answer."""
            v = support.view(app, user)
            self._support_view, self._support_path = (None if v.plain else v), path
            if not v.plain:
                for key in ("client", "case"):
                    if key in q:
                        q[key] = v.unalias(q[key])
            case = str(q.get("client") or q.get("case") or "")
            case = case if case and "\0" not in case and (Path(app.data_root) / case).is_dir() else None
            refused = ""
            if method != "GET" and path != "/api/logout":
                refused = support.REFUSED_WRITE
            elif path in SUPPORT_NEVER:
                refused = "this page is not shown to support"
            elif not v.plain and (path.endswith((".pdf", ".zip", ".csv", ".json")) or path in SUPPORT_FILES):
                refused = support.REFUSED_FILE
            try:
                support.note(app, user, method, path, case, refused)
            except OSError as exc:  # the record could not be written (a full disk): nothing is answered
                sys.stderr.write(f"support request not recorded, so it was not answered: {type(exc).__name__}\n")
                self._json({"error": VIEW_LOG_FAILED}, 503)
                return False
            if refused:
                self._json({"error": refused}, 403)
                return False
            return True

        def _support_get(self, path: str, q: dict, user: dict | None) -> None:
            """GET /api/support (the attorney's Settings section: the current session and the last ten), and the pages made for support itself:
            /api/support/health, /api/support/cases, /api/support/case?case=Case N (src/support.py)."""
            if path == "/api/support":
                if app.accounts is None or not user or user["role"] != "attorney":
                    return self._json({"error": "Letting support in is an attorney's."}, 403)
                rows = app.accounts.support_accounts()
                current = [r for r in rows if r.get("open")]
                return self._json({"current": current, "last": [r for r in rows if not r.get("open")][:10],
                                   "code_set_up": bool(user.get("code_set_up")), "hours": list(range(1, 9))})
            if not support.is_support(user):
                return self._json({"error": "These pages are support's own."}, 403)
            if path == "/api/support/health":
                return self._json(support.health(app))
            if path == "/api/support/cases":
                return self._json(support.cases(app, user))
            case = str(q.get("case") or "")
            if not case or not app.may_open(user, case) or not (Path(app.data_root) / case).is_dir():
                return self._json(UNKNOWN, 404)
            self._json(support.case_shape(app, user, case))

        def _support_post(self, body: dict, user: dict | None) -> None:
            """POST /api/support: an attorney with the sign-in code set up lets support in ({"action": "let_in", name, email, hours, plain}: the
            one-time code shown once, never sent), or ends a session early ({"action": "end", email})."""
            if app.accounts is None or not user or user["role"] != "attorney":
                raise PermissionError("Letting support in is an attorney's.")
            if body.get("action") == "end":
                if not app.accounts.end_support(str(body.get("email") or ""), user["name"]):
                    raise LookupError("No support session is open under that address.")
                return self._json({"ok": True})
            if not user.get("code_set_up"):
                raise PermissionError("Set up your sign-in code first (Settings, Sign-in codes): only an attorney with the second factor on lets support in.")
            code, session = app.accounts.let_support_in(str(body.get("email") or ""), str(body.get("name") or ""), body.get("hours"),
                                                        bool(body.get("plain")), user["name"])
            self._json({"code": code, "session": session, "email": str(body.get("email") or "").strip().lower()})

        def do_GET(self):
            self._answered = False
            if not self._guard():
                return
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if self._calendar(url.path):  # a calendar address: its token is the credential (TOKENED)
                return
            self._support_view = None
            ok, user = self._signed_in(url.path)
            if not ok:
                return
            if support.is_support(user) and not self._support_request("GET", url.path, q, user):  # recorded first; refused or masked (src/support.py)
                return
            app.after_purges()  # a purge the job worker ran: the app's copies in memory forget the case (src/purge.py)
            if app.accounts is not None and user is None and url.path in OPEN_GETS and app.limited(url.path, self._address()):
                self.close_connection = True  # no session, asked again and again: a flood (signed-in staff are never counted)
                return self._json({"error": TOO_MANY}, 429)
            try:
                if url.path == "/":
                    import settings

                    name = settings.firm_name()  # the firm's own name in the tab title and the top bar; "Case Review" alone when none is saved
                    html = (STATIC / "index.html").read_text(encoding="utf-8").replace("%%TITLE%%", escape(f"Case Review · {name}" if name else "Case Review")) \
                        .replace("%%MARK%%", escape(settings.firm_mark(name))).replace("%%FIRM%%", escape(name)).encode("utf-8")
                    self._send(200, html, "text/html; charset=utf-8")  # the Content-Security-Policy with this answer's nonce (_headers)
                elif url.path == "/api/drive-settings":
                    self._answer(app.drive_settings, user)
                elif url.path == "/api/me":
                    # the installation's support and version, once signed in (never to someone who isn't)
                    about = __import__("deployment").about() if (user or app.accounts is None) else None
                    out = {"accounts": app.accounts is not None, "user": user, "firm": __import__("settings").firm_name()} | ({"about": about} if about else {})
                    if user or app.accounts is None:  # the office's zone: the page writes every date and time in it (src/clock.py)
                        out["time_zone"] = clock.zone_name()
                    if app.accounts is not None and user is None:  # the sign-in screen: its Microsoft / Google buttons, and why the last try failed
                        providers = app.sign_in_providers()
                        out |= ({"sign_in_with": providers} if providers else {}) | (
                            {"sign_in_error": SIGN_IN_MESSAGES[q["signin"]]} if q.get("signin") in SIGN_IN_MESSAGES else {})
                    if user and app.accounts is not None and __import__("getting_started").first_visit(user):
                        out["first_sign_in"] = True  # the screen opens Getting started once for an attorney
                    if app.accounts is not None and user is None and app.accounts.needs_setup():
                        # "Set up the first attorney" instead of the sign-in screen: to an address that carries a printed code, or to this computer
                        # with a box for the code (the code is always needed: brief J2); nobody else is told the installation has no account yet
                        code = str(q.get("setup") or "")
                        if app.accounts.setup_open():
                            if code and not app.limited("/api/setup", self._address()) and app.accounts.setup_code_ok(code):
                                out["setup"] = {"allowed": True}
                            elif self._same_machine():
                                out["setup"] = {"allowed": True, "code_needed": True}
                    self._json(out)
                elif url.path == "/auth/start":
                    self._auth_start(q)
                elif url.path == "/auth/callback":
                    self._auth_callback(q)
                elif url.path in ("/api/support", "/api/support/health", "/api/support/cases", "/api/support/case"):  # Let support in; support's own pages
                    self._support_get(url.path, q, user)
                elif url.path in CASE_GET and not app.may_open(user, str(q.get("client") or "")):
                    # a case this person may not open, or no case at all (src/restricted.py): one answer for both
                    self._json(UNKNOWN, 404)
                elif url.path == "/api/drive-selection":
                    self._answer(app.drive_selection, str(q.get("client") or ""), user)
                elif url.path == "/api/access_log":  # the attorney's "Who viewed this": the latest rows, or every row a page at a time (&page=)
                    self._answer(lambda: app.access_log(q["client"], user["role"] if user else None, int(q.get("limit") or 50),
                                                        app._page_of(q) if "page" in q else None, str(q.get("person") or ""), str(q.get("kind") or "")))
                elif url.path == "/api/access_log.csv":  # all of a case's rows (or those the person and kind match) as a CSV file
                    self._answer(lambda: app.access_log_csv(q["client"], user["role"] if user else None, str(q.get("person") or ""), str(q.get("kind") or "")),
                                 csv="who-viewed-this-case.csv")
                elif url.path == "/api/case_events":  # the case page's "What changed on this case" (src/events.py): the latest rows, or every row a page at a time
                    self._answer(app.case_events, q["client"], user, q)
                elif url.path == "/api/case_events.csv":
                    self._answer(app.case_events, q["client"], user, q, True, csv="what-changed-on-this-case.csv")
                elif url.path == "/api/g28":  # the G-28 card: the address, Part 4, the other forms' mailing address (src/g28.py)
                    self._json({"card": app.g28_card(q["client"], q.get("filing"))})
                elif url.path == "/api/rebuild":  # the case's "Rebuild the forms" card after a release (src/rebuild.py)
                    self._json({"card": app.rebuild_card(q["client"], q.get("filing"))})
                elif url.path == "/api/audit-fill":  # the boxes the office changed on this case, with their values: the case's own page only (src/audit_fill.py)
                    self._json(app.audit_case(q["client"]))
                elif url.path == "/api/case-notes":  # the case's notes and tasks (src/case_notes.py)
                    self._json(app.case_notes(q["client"], user))
                elif url.path == "/api/apply-for":  # what could this person apply for, on a case (src/apply_for.py): the questions and the answers, never a conclusion
                    self._json(app.apply_for_view(q["client"]))
                elif url.path == "/api/prospects":  # Prospects, on All clients (src/prospects.py): a page at a time, those this person may open
                    self._answer(app.prospects_list, q, user)
                elif url.path == "/api/prospects.csv":
                    self._answer(app.prospects_list, q, user, True, csv="prospects.csv")
                elif url.path == "/api/prospect":  # one prospect's page: gated inside (a restricted one is unknown to anyone who may not open it)
                    from portal.communication_consent import data_gate
                    with data_gate(app.data_root.parent):
                        ok, user = self._signed_in(url.path)
                        if not ok:
                            return
                        view = app.prospect_view(str(q.get("prospect") or ""), user)
                    if self._viewed(user, "prospect:" + str(q.get("prospect") or ""), "prospect"):
                        self._json(view)
                elif url.path == "/api/prospect-letter.pdf":  # the non-engagement letter made when the firm declined the prospect
                    pdf = app.prospect_letter(str(q.get("prospect") or ""), q.get("id"), user).read_bytes()
                    if self._viewed(user, "prospect:" + str(q.get("prospect") or ""), "prospect_letter"):
                        self._send(200, pdf, "application/pdf")
                elif url.path == "/api/case-questions":  # Ask about this case (src/case_questions.py): the switch, the practice, the latest questions
                    self._json(app.case_questions(q["client"]))
                elif url.path == "/api/case-summary.pdf":  # a summary for the attorney, drawn from the case's question log (DRAFT, never filed)
                    import case_questions

                    pdf = case_questions.summary_pdf(app.client_dir(q["client"]), str(q.get("id") or ""))
                    if self._viewed(user, q["client"], "case_summary"):
                        self._send(200, pdf, "application/pdf", {"Content-Disposition": 'attachment; filename="summary-for-the-attorney-draft.pdf"'})
                elif url.path == "/api/firm_events":  # Staff, "What changed across the firm": the attorney's
                    self._answer(app.firm_events, q, user)
                elif url.path == "/api/firm_events.csv":
                    self._answer(app.firm_events, q, user, True, csv="what-changed-across-the-firm.csv")
                elif url.path == "/api/conflicts":  # Settings, "Conflict checks" (src/conflicts.py): the attorney's
                    self._answer(app.conflict_checks, q, user)
                elif url.path == "/api/conflicts.csv":
                    self._answer(app.conflict_checks, q, user, True, csv="conflict-checks.csv")
                elif url.path == "/api/export-firm":  # Keeping current, "Export the firm's data": how it is going, and the earlier exports (the attorney's)
                    self._answer(app.export_state, user)
                elif url.path == "/api/export-firm.zip":  # one finished export, streamed (it can be many gigabytes)
                    self._export_download(q, user)
                elif url.path == "/api/clients":
                    self._json(app.clients(user))
                elif url.path == "/api/case-list":
                    self._answer(app.case_list, user, q)
                elif url.path == "/api/assignment":
                    self._answer(app.assignment_detail, q.get("client") or "", user)
                elif url.path == "/api/overview":  # All clients: a page of rows, filtered here, with the counts (?page=, ?stage=, ?q= ...)
                    self._answer(app.overview, user["role"] if user else None, user, q)
                elif url.path == "/api/deadlines":  # What's due, Deadlines: a page at a time
                    self._answer(app.deadlines_page, user["role"] if user else None, user, q)
                elif url.path == "/api/jobs":  # the readings on one case (the Documents tab's "Reading now")
                    self._json(app.jobs_of(q["client"]))
                elif url.path == "/api/staff-upload-outcome":
                    self._answer(app.staff_upload_outcome, q["client"], q.get("attempt", ""), user)
                elif url.path == "/api/jobs/firm":  # the readings that belong to no case (the inbox), and how many are waiting across the firm
                    self._json(app.jobs_firm(user, q))
                elif url.path == "/api/staff":  # the Settings page's Staff section (the attorney's)
                    self._json(app.staff(user))
                elif url.path == "/api/calendar":  # Settings, My calendar: the person's own address state and reminders switch
                    self._json(app.calendar_state(user))
                elif url.path == "/api/month":  # What's due, Month view: the cases this person may see
                    try:
                        self._json(app.month(q, user))
                    except ValueError as exc:  # a month that is not one
                        return self._json({"error": str(exc)}, 400)
                elif url.path == "/api/getting-started":  # the attorney's first-day page: Done states read from the firm's data
                    self._json(app.getting_started(user))
                elif url.path == "/api/staff_log":  # Staff, "What staff did" (review/oversight.py): the attorney's
                    self._answer(app.staff_did, q, user)
                elif url.path == "/api/staff_log.csv":
                    self._answer(app.staff_did, q, user, True, csv="what-staff-did.csv")
                elif url.path == "/api/views":  # Staff, "Who viewed what": every opening across the firm, or the totals by person and day
                    self._answer(app.who_viewed, q, user)
                elif url.path == "/api/views.csv":
                    self._answer(app.who_viewed, q, user, True, csv="who-viewed-what.csv")
                elif url.path == "/api/messages_on":  # Staff, restricted cases that send automatic messages
                    self._answer(app.messages_on_cases, user)
                elif url.path == "/api/policy_changes":  # Firm policies, "Every change"
                    self._answer(app.policy_changes, user)
                elif url.path == "/api/policy_changes.csv":
                    self._answer(app.policy_changes, user, True, csv="firm-policy-changes.csv")
                elif url.path == "/api/maintenance":
                    self._json(app.maintenance())
                elif url.path == "/api/settings":
                    self._json(app.settings(user["role"] if user else None))
                elif url.path == "/api/connections":  # Settings, Connections (connectors/clio.py): the attorney's
                    self._json(app.connections(user["role"] if user else None) | ({"message": CLIO_MESSAGES[q["clio"]]} if q.get("clio") in CLIO_MESSAGES else {}))
                elif url.path == "/api/learning":
                    self._json(app.learning(user))
                elif url.path == "/api/contact-recovery":
                    self._answer(app.contact_recovery, q, user)
                elif url.path == "/api/promotion-recovery":
                    self._answer(app.promotion_recovery, q, user)
                elif url.path == "/api/communication":
                    self._answer(app.authenticated_communication_view, q["client"], self._token())
                elif url.path == "/api/prospect-communication":
                    self._answer(app.authenticated_communication_view, q.get("prospect", ""), self._token(), True)
                elif url.path == "/api/client-wording":
                    self._answer(app.client_wording, q, user)
                elif url.path == "/api/requests":
                    self._json(app.client_requests(q["client"]))
                elif url.path == "/api/messages":
                    self._json(app.client_messages(q["client"]))
                elif url.path == "/api/items":
                    from portal.communication_consent import data_gate
                    with data_gate(app.data_root.parent):
                        ok, user = self._signed_in(url.path)
                        if not ok:
                            return
                        if not app.may_open(user, q["client"]):
                            return self._json(UNKNOWN, 404)
                        items = app.items(q["client"], user)
                    # Expensive optional reading holds neither the installation
                    # communication gate nor the app lock. Only verified source
                    # snapshots are used; authority is checked again on release.
                    from review.evidence import annotate, revalidate_locations
                    deadline, preview_pages, quote_count = time.monotonic() + 10, set(), [0]
                    def optional_region(source, location, quote):
                        key = (source.sha256, location.get("page"))
                        if time.monotonic() >= deadline or quote_count[0] >= 24 or key not in preview_pages and len(preview_pages) >= 3:
                            return None
                        quote_count[0] += 1
                        preview_pages.add(key)
                        return app.source_quote_region(q["client"], source, location, quote, deadline=deadline)
                    annotate(app.client_dir(q["client"]), items,
                             region_reader=optional_region,
                             preserve_identity=True)
                    with data_gate(app.data_root.parent):
                        ok, user = self._signed_in(url.path)
                        if not ok:
                            return
                        if not app.may_open(user, q["client"]):
                            return self._json(UNKNOWN, 404)
                        revalidate_locations(app.client_dir(q["client"]), items)
                        if self._viewed(user, q["client"], "case"):  # the row first, then the answer (a send that dies loses no row)
                            self._json(items)
                elif url.path == "/api/crop":
                    from review.bundle import crop as crop_box

                    source = app.source_snapshot(q["client"], q["doc"], q.get("expected_sha256"))
                    image = app.page_image(q["client"], q["doc"], int(q["page"]), q.get("expected_sha256"))
                    if q.get("box"):
                        from review.evidence import validate_region
                        validate_region(q["box"], image.width, image.height)
                    crop = crop_box(image, q["box"]) if q.get("box") else image  # the same crop the review bundle prints
                    # The inline preview is bounded; enlargement still requests
                    # the original-resolution source/crop, with the same hash.
                    if q.get("preview") == "1":
                        from PIL import Image
                        crop = crop.copy()
                        crop.thumbnail((850, 1200), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    crop.save(buf, format="PNG")
                    from portal.communication_consent import data_gate
                    from review.evidence import Unavailable
                    with data_gate(app.data_root.parent):
                        ok, user = self._signed_in(url.path)
                        if not ok:
                            return
                        if support.is_support(user):
                            if not support.plain(user):
                                support.note(app, user, "GET", url.path, q["client"], support.REFUSED_FILE)
                                return self._json({"error": support.REFUSED_FILE}, 403)
                            if not self._support_request("GET", url.path, dict(q), user):
                                return
                        if not app.may_open(user, q["client"]):
                            return self._json(UNKNOWN, 404)
                        current = app.source_snapshot(q["client"], q["doc"], source.sha256)
                        if current.location(int(q["page"]))["page"] is None:
                            raise Unavailable("The original page location changed. Refresh the evidence before opening its preview.")
                        if self._viewed(user, q["client"], "scan", q["doc"]):
                            self._send(200, buf.getvalue(), "image/png")
                elif url.path == "/api/answers":
                    page = app.answers_page(q["client"]).encode("utf-8")
                    if self._viewed(user, q["client"], "answers"):
                        self._send(200, page, "text/html; charset=utf-8")
                elif url.path == "/api/questionnaire.pdf":
                    try:
                        data = app.questionnaire_pdf(q["client"])
                    except ValueError as exc:
                        return self._json({"error": str(exc)}, 409)
                    from portal.communication_consent import data_gate
                    with data_gate(app.data_root.parent):
                        ok, user = self._signed_in(url.path)
                        if not ok:
                            return
                        if support.is_support(user):
                            if not support.plain(user):
                                support.note(app, user, "GET", url.path, q["client"], support.REFUSED_FILE)
                                return self._json({"error": support.REFUSED_FILE}, 403)
                            if not self._support_request("GET", url.path, dict(q), user):
                                return
                        if not app.may_open(user, q["client"]):
                            return self._json(UNKNOWN, 404)
                        if self._viewed(user, q["client"], "answers", "submitted-questionnaire.pdf"):
                            self._send(200, data, "application/pdf", {"Content-Disposition": 'attachment; filename="submitted-questionnaire.pdf"'})
                elif url.path == "/api/file":
                    source = app.source_snapshot(q["client"], q["doc"], q.get("expected_sha256"))
                    if self._viewed(user, q["client"], "document", source.path.name):
                        self._send(200, source.data, source.content_type)
                elif url.path == "/api/document-text":
                    text = app.document_text(q["client"], q.get("id") or "")
                    if self._viewed(user, q["client"], "documents", text["id"]):
                        self._json(text)
                elif url.path == "/api/capture-image":
                    data, content_type = app.capture_image(q["client"], q.get("upload") or "", q.get("kind") or "")
                    from portal.communication_consent import data_gate
                    with data_gate(app.data_root.parent):
                        ok, user = self._signed_in(url.path)
                        if not ok:
                            return
                        if support.is_support(user):
                            if not support.plain(user):
                                support.note(app, user, "GET", url.path, q["client"], support.REFUSED_FILE)
                                return self._json({"error": support.REFUSED_FILE}, 403)
                            if not self._support_request("GET", url.path, dict(q), user):
                                return
                        if not app.may_open(user, q["client"]):
                            return self._json(UNKNOWN, 404)
                        if self._viewed(user, q["client"], "scan"):
                            self._send(200, data, content_type)
                elif url.path == "/api/documents":  # the case's documents: whose, language, dates, quality (src/documents.py)
                    docs = app.documents(q["client"])
                    if self._viewed(user, q["client"], "documents"):
                        self._json(docs)
                elif url.path == "/api/absence":  # the papers a filing asks about, and the ones the client has none of (src/absence.py)
                    marks = app.absence(q["client"], q.get("filing"))
                    if self._viewed(user, q["client"], "documents"):  # it shows the client's own answers: logged as the Documents list is
                        self._json(marks)
                elif url.path == "/api/packet":
                    self._json(app.packet_plan(q["client"], q.get("filing")))
                elif url.path == "/api/translation.pdf":  # a foreign document's translation and certificate (src/translation.py)
                    import translation

                    if not re.fullmatch(r"[0-9a-f]{16}", q.get("id") or ""):
                        raise ValueError("bad document id")
                    pdf = translation.pdf_path(app.client_dir(q["client"]), q["id"])
                    data = pdf.read_bytes()
                    if self._viewed(user, q["client"], "translation", pdf.name):
                        self._send(200, data, "application/pdf")
                elif url.path == "/api/declaration.pdf":  # the client's declaration, as the packet holds it (src/drafting.py)
                    import drafting

                    if q.get("filing") not in drafting.FILINGS:
                        raise ValueError("bad filing")
                    pdf = drafting.current_pdf(app.client_dir(q["client"]), q["filing"])  # never a copy of text that changed since
                    data = pdf.read_bytes()
                    if self._viewed(user, q["client"], "declaration", pdf.name):
                        self._send(200, data, "application/pdf")
                elif url.path == "/api/packet.pdf":
                    import packet as packet_module

                    pdf = app.client_dir(q["client"]) / packet_module.load_filing(q.get("filing")).get("packet_pdf", "packet.pdf")
                    data = pdf.read_bytes()
                    if self._viewed(user, q["client"], "packet", pdf.name):
                        self._send(200, data, "application/pdf")
                elif url.path == "/api/online-bundle.zip":  # the files to upload in the USCIS online account (src/online_filing.py)
                    import online_filing
                    import packet as packet_module

                    filing = packet_module.load_filing(q.get("filing")).get("filing", "i485")  # a known filing only
                    bundle_zip = online_filing.bundle_paths(app.client_dir(q["client"]), filing)[0]
                    data = bundle_zip.read_bytes()
                    if self._viewed(user, q["client"], "online_bundle", bundle_zip.name):
                        self._send(200, data, "application/zip", {"Content-Disposition": f'attachment; filename="{re.sub(r"[^A-Za-z0-9_-]", "", q["client"])}-{filing}-online.zip"'})
                elif url.path == "/api/review-bundle.pdf":  # the review bundle behind a packet: attorney or paralegal (src/review/bundle.py)
                    from review import bundle

                    pdf = bundle.paths(app.client_dir(q["client"]), q.get("filing") or None)[0]
                    data = pdf.read_bytes()
                    if self._viewed(user, q["client"], "review_bundle", pdf.name):
                        self._send(200, data, "application/pdf")
                elif url.path == "/api/rules":
                    self._json(app.rules())
                elif url.path == "/api/front-desk":  # what the Add a client form offers
                    self._json(app.front_desk())
                elif url.path == "/api/policies":  # the firm's standard answers (Settings)
                    self._json(app.policies(user["role"] if user else None))
                elif url.path in ("/api/accuracy", "/api/accuracy.pdf"):  # the attorney's accuracy record (review/learning.py accuracy)
                    try:
                        report = app.accuracy(q, user["role"] if user else None)
                    except PermissionError as exc:
                        return self._json({"error": str(exc)}, 403)
                    except ValueError as exc:  # a period that isn't one
                        return self._json({"error": str(exc)}, 400)
                    if url.path == "/api/accuracy":
                        self._json(report)
                    else:
                        import offices
                        from review import bundle

                        firm = offices.offices()[0]
                        pdf = bundle.accuracy_pdf(report, firm["values"].get("firm.business_name") or firm["name"], user["name"] if user else "")
                        self._send(200, pdf, "application/pdf", {"Content-Disposition": 'inline; filename="accuracy-record.pdf"'})
                elif url.path == "/api/accuracy/references":  # the attorney's: the comparison with hand-filled references (src/accuracy.py)
                    try:
                        self._json(app.accuracy_references(user))
                    except PermissionError as exc:
                        return self._json({"error": str(exc)}, 403)
                elif url.path == "/api/i360":
                    self._json(app.i360_status(q["client"]))
                elif url.path == "/api/rfe":  # a USCIS request and its response (src/rfe.py)
                    self._json(app.rfe(q["client"], q.get("key")))
                elif url.path == "/api/rfe.pdf":
                    import rfe as rfe_module

                    key = q["key"]
                    if not re.fullmatch(r"[A-Z0-9]{3,13}_[a-z]+_[0-9-]+|[A-Z0-9]{3,13}_[a-z]+_undated", key):
                        raise ValueError("bad request key")
                    data = (rfe_module._dir(app.client_dir(q["client"])) / f"{key}_response.pdf").read_bytes()
                    if self._viewed(user, q["client"], "rfe_response", f"{key}_response.pdf"):
                        self._send(200, data, "application/pdf")
                elif url.path == "/api/journey":
                    self._json(app.journey(q["client"], user) | {"clio": app.clio_case(q["client"], user["role"] if user else None)})  # the case page's "In Clio" line
                elif url.path == "/api/family-candidates":
                    self._answer(app.family_candidates, q["client"], q, user)
                elif url.path == "/api/family-link-recovery":
                    self._answer(app.family_recovery, q["client"], user)
                elif url.path == "/api/path":  # the case's Path tab (src/path.py): the template, the approved path, a waiting change, and what each derives
                    self._json(app.path_view(q["client"], user))
                elif url.path == "/api/engagement":  # the case's agreement, its letters and its end (src/engagement.py)
                    self._json(app.engagement_view(q["client"], user))
                elif url.path == "/api/engagement.pdf":  # one letter to the client, or the paper copy the client signed
                    if q.get("copy") in ("signed", "certificate"):
                        import signing_evidence

                        d = app._engagement_dir(q["client"])
                        letter_id = str(q.get("id") or "")
                        # CASE_GET has already enforced restricted case access.
                        if q.get("copy") == "certificate":
                            data = signing_evidence.certificate(d, letter_id)
                        else:
                            verified = signing_evidence.verify(d, letter_id)
                            if not verified["ok"]:
                                raise ValueError("The signing record is unverifiable; inspect the certificate.")
                            data = signing_evidence.paths(d, letter_id)[0].read_bytes()
                            if signing_evidence.digest(data) != verified["letter_sha256"]:
                                raise ValueError("Signed letter changed during retrieval.")
                        if self._viewed(user, q["client"], "letter", "signing-evidence.pdf"):
                            self._send(200, data, "application/pdf")
                    else:
                        pdf = app.engagement_pdf(q["client"], q.get("id"), q.get("copy") == "paper")
                        data = pdf.read_bytes()
                        if self._viewed(user, q["client"], "letter", pdf.name):
                            self._send(200, data, "application/pdf")
                elif url.path == "/api/eoir26a":  # the case's fee waiver request: figures and their sources, item 4, the signing, the attestation (src/eoir26a.py)
                    self._json(app.eoir26a_view(q["client"], user))
                elif url.path == "/api/explain":  # the explanations of Part 9's Yes answers in Part 14 (src/part14_explain.py)
                    self._json(app.explain_view(q["client"], user))
                elif url.path == "/api/eoir26a.pdf":  # the form as it stands (with the signing record), or the paper copy the client signed
                    pdf = app.eoir26a_pdf(q["client"], q.get("copy") == "paper")
                    data = pdf.read_bytes()
                    if self._viewed(user, q["client"], "fee_waiver_form", pdf.name):
                        self._send(200, data, "application/pdf")
                elif url.path == "/api/prepare-sheet.pdf":  # an appointment's preparation sheet, in the client's language, for the office to print (src/client_case.py)
                    sheet = app.prepare_sheet(q["client"], q.get("id") or "", q.get("lang"))
                    if self._viewed(user, q["client"], "prepare_sheet", "preparation-sheet.pdf"):
                        self._send(200, sheet, "application/pdf", {"Content-Disposition": 'inline; filename="preparation-sheet.pdf"'})
                elif url.path == "/api/case-file.zip":  # the client's file to hand over (the attorney's), streamed
                    from portal.communication_consent import data_gate
                    import client_file
                    import engagement
                    import oslock
                    with data_gate(app.data_root.parent), app.case_write(q["client"]), app._lock:
                        ok, user = self._signed_in(url.path)
                        if not ok:
                            return
                        if not app.may_open(user, q["client"]):
                            return self._json(UNKNOWN, 404)
                        with oslock.locked(app._engagement_dir(q["client"]) / "client-file.lock", timeout=30, poll=0.02):
                            made = app.case_file(q["client"], user)
                            # Pin the record before capture; validated paths cannot
                            # authorize replacement bytes from an external writer.
                            prepared = engagement.read(app._engagement_dir(q["client"])).get("file") or {}
                            payload = client_file.capture(made, engagement.exports_folder(app.data_root))
                            client_file.inspect_bytes(payload, prepared.get("sha256") or "")
                    if self._viewed(user, q["client"], "case_file", "client-file.zip"):
                        self._send(200, payload, "application/zip", {"Content-Disposition": 'attachment; filename="client-file.zip"'})
                elif url.path == "/api/wordings":  # Settings, Firm wordings: the library learned from approvals (src/wordings.py)
                    self._json(app.wordings_view(user))
                elif url.path == "/api/find":  # Find across the firm (src/find.py): the switch, and for an attorney who asked what; no case in it
                    self._json(app.find_view(user))
                elif url.path == "/api/purge":  # the purge of one case (src/purge.py): the attorney's screen; a paralegal gets 403
                    self._json(app.purge_view(q["client"], user))
                elif url.path == "/api/retention-rules":  # Settings, Keeping closed files (src/purge.py): the attorney's
                    self._json(app.retention_rules(user))
                elif url.path == "/api/wordings.json":  # the library as one file to take away: the attorney's
                    self._send(200, app.wordings_export(user), "application/json; charset=utf-8", {"Content-Disposition": 'attachment; filename="firm-wordings.json"'})
                elif url.path == "/api/firm-documents":  # Settings, Firm documents: every office's letters (src/engagement.py)
                    self._json(app.firm_documents(user))
                elif url.path == "/api/retention":  # Keeping current: ended cases past their keeping date (the attorney's)
                    self._answer(app.retention_list, user)
                elif url.path == "/api/prefile":
                    self._json(app.prefile(q["client"], q.get("filing")))
                elif url.path == "/api/search":  # documents across every case (src/index.py)
                    try:
                        self._json(app.search(q, user["role"] if user else None, user))
                    except ValueError as exc:  # a date that isn't one
                        return self._json({"error": str(exc)}, 400)
                elif url.path == "/api/approvals":  # the attorney's queue: every open item that needs an attorney (src/approvals.py); attorneys only
                    self._answer(app.approvals_queue, q, user)
                elif url.path == "/api/health":
                    self._json(app.health(user))
                elif url.path == "/api/posture":  # Settings, "This computer": the machine's duties as last read (src/posture.py); the attorney's
                    self._json(app.posture(user))
                elif url.path == "/api/today":  # Today: the paralegal's day plan (src/day_plan.py); the cases this person may open
                    self._answer(app.today, q, user)
                elif url.path == "/api/work":
                    self._answer(app.work, q.get("owner") or (user["role"] if user else None), user["role"] if user else None, user, q.get("limit"))
                elif url.path == "/api/inbox":  # the notice inbox's queue and what it routed lately (src/inbox.py)
                    self._json(app.inbox_view(user))
                elif url.path == "/api/inbox/cases":
                    self._json(app.inbox_cases(q, user))
                elif url.path == "/api/inbox/file":  # a waiting notice's scan, to look at before placing it
                    scan = app.inbox_file(q["id"], user)
                    if self._viewed(user, "", "notice", str(q["id"])):  # the row first (F13: a waiting notice carries an A-Number and a receipt number)
                        self._send(200, scan, "application/pdf")
                elif url.path == "/api/reports":  # counts and lists, for the attorney and the paralegal (review/reports.py)
                    self._json(app.reports(user["role"] if user else None, user))
                elif url.path == "/api/reports.csv":
                    try:
                        body = app.report_csv(str(q.get("table") or ""), user["role"] if user else None, user)
                    except LookupError as exc:
                        return self._json({"error": str(exc)}, 404)
                    self._send(200, body, "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="report-{re.sub(r"[^a-z_]", "", q["table"])}.csv"'})
                elif url.path == "/api/expiring":  # What's due, Expiring documents (src/expiry.py)
                    try:
                        self._json(app.expiring(q, user["role"] if user else None, user))
                    except ValueError as exc:
                        return self._json({"error": str(exc)}, 400)
                elif url.path == "/api/form":  # a filled companion form (src/fill/companion.py)
                    # every form's own output file (schemas/packets/companion_forms.json) -- a name not on the list is refused
                    import packet as packets

                    made = {fid: g["output"] for f in packets.FILINGS for fid, g in (packets.load_filing(f).get("generated") or {}).items()}  # the visa answer sheet
                    name = "i485_filled.pdf" if q["form"] == "i485" else made.get(q["form"]) or packets.form_output(q["form"])  # one per person too (i864a_1)
                    data = (app.client_dir(q["client"]) / name).read_bytes()
                    if self._viewed(user, q["client"], "filled_form", name):
                        self._send(200, data, "application/pdf")
                elif url.path == "/api/filled":
                    pdf = app.client_dir(q["client"]) / "i485_filled.pdf"
                    data = pdf.read_bytes()
                    if self._viewed(user, q["client"], "filled_form", pdf.name):
                        self._send(200, data, "application/pdf")
                else:
                    self._send(404, b"not found", "text/plain")
            except TimeoutError:
                self._json({"error": "The operation timed out. Reread current state before trying again.", "busy": True}, 503)
            except PermissionError as exc:
                self._json({"error": str(exc)}, 403)
            except (LookupError, KeyError, IndexError, ValueError, FileNotFoundError) as exc:
                self._json({"error": str(exc) or "not found"}, 404)
            except Exception:  # noqa: BLE001 -- said on the console; the person gets a 500 in words
                self._failed()

        def _webhook(self, path: str) -> bool:
            """Clio's webhook (WEBHOOKS): answered here before the X-Review-App and sign-in checks, because Clio sends neither. The signature on the body is the
            credential (src/connectors/clio_hooks.py): any call that does not pass it, is a replay, is too large or comes while nothing is subscribed gets
            the answer a made-up POST path gets. Rate-limited per address like the sign-in routes: all calls, and the refused ones apart. True when the path
            was the webhook's."""
            if path not in WEBHOOKS:
                return False
            from connectors import clio_hooks

            def refuse():
                if app.limited("/clio/webhook-bad", self._address()):
                    self.close_connection = True
                    return self._json({"error": TOO_MANY_REQUESTS}, 429)
                self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")  # the same call, the same answer as a path that does not exist

            if app.limited("/clio/webhook", self._address()):
                self.close_connection = True
                self._json({"error": TOO_MANY_REQUESTS}, 429)
                return True
            length = (self.headers.get("Content-Length") or "").strip()
            if not (length.isascii() and length.isdigit()) or int(length) > clio_hooks.MAX_BODY:
                self.close_connection = True  # the body is never read
                refuse()
                return True
            body = self.rfile.read(int(length))
            kind, headers = app.clio_webhook(body, self.headers.get("X-Hook-Signature"), self.headers.get("X-Hook-Secret"))
            if kind == "refuse":
                refuse()
            else:
                self._send(200, b"{}", "application/json; charset=utf-8", headers)
            return True

        def do_POST(self):
            self._answered = False
            if not self._guard():
                return
            url = urlparse(self.path)
            if self._webhook(url.path):  # Clio's webhook: its signature is the credential (WEBHOOKS)
                return
            if self.headers.get("X-Review-App") != "1" or not (self.headers.get("Content-Type") or "").startswith("application/json"):
                self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
                return
            # the body's size, checked before a byte of it is read: signing in (open to anyone) gets the least
            length = (self.headers.get("Content-Length") or "0").strip()
            if not (length.isascii() and length.isdigit()):  # isdigit() alone lets "²" through to int()
                self._send(HTTPStatus.BAD_REQUEST, b"bad length", "text/plain")
                return
            if int(length) > (MAX_BODY_OPEN if url.path in open_paths else MAX_BODY_UPLOAD if url.path in ("/api/client-upload", "/api/engagement-paper", "/api/eoir26a-paper") else MAX_BODY_EVIDENCE if url.path in ("/api/communication", "/api/prospect-communication", "/api/client-wording") else MAX_BODY):
                self.close_connection = True
                self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"request too large", "text/plain")
                return
            bucket = ATTEMPTS_AS.get(url.path, url.path)
            if app.accounts is not None and bucket in ATTEMPTS and app.limited(bucket, self._address()):
                self.close_connection = True  # the body is never read
                self._json({"error": TOO_MANY}, 429)
                return
            self._support_view = None
            ok, user = self._signed_in(url.path)
            if not ok:
                return
            if support.is_support(user) and not self._support_request("POST", url.path, {}, user):  # support writes nothing: 403 but signing out
                return
            app.after_purges()  # a purge the job worker ran: the app's copies in memory forget the case (src/purge.py)
            stack, body = ExitStack(), None  # a case's lock (src/jobs.py), held for the length of a write to it
            try:
                body = json.loads(self.rfile.read(int(length)) or b"{}")
                if not isinstance(body, dict):  # a list or a bare value: said in words (400), never a dropped connection
                    raise ValueError("The request wasn't in the form the app sends. Reload the page and try again.")
                if app.accounts is not None:
                    if url.path in ("/api/login", "/api/password", "/api/code", "/api/enrol"):
                        return self._account(url.path, body)
                    if url.path == "/api/logout":
                        app.accounts.sign_out(self._token())
                        return self._json({"ok": True}, extra=self._cookie("", 0))
                    if url.path == "/api/setup":  # the first attorney, from the screen (open to anyone who passes create_first's checks)
                        return self._setup(body)
                    body["reviewer"] = user["name"]  # who decided is the signed-in person, never a typed name
                role = user["role"] if user else None
                events.set_actor(body.get("reviewer"), role)  # the event ledger's "who" for everything this request writes (src/events.py)
                client = body.get("client", "")
                if url.path == "/api/document" and body.get("field") in ("boundaries", "boundary_undo"):
                    from classify.classifier import page_cache, cached_pdf_pages
                    from portal.communication_consent import data_gate
                    import document_instances
                    stack.enter_context(page_cache())
                    with data_gate(app.data_root.parent), app.case_write(client, url.path):
                        ok, user = self._signed_in(url.path)
                        if not ok:
                            return
                        if not app.may_open(user, str(client or "")):
                            return self._json(UNKNOWN, 404)
                        original = document_instances.boundary_snapshot(app.client_dir(client), str(body.get("id") or ""), str(body.get("fingerprint") or ""))
                    cached_pdf_pages(original)  # Slow OCR holds neither installation nor case writer locks.
                if url.path in GATED_POST:
                    from portal.communication_consent import data_gate
                    stack.enter_context(data_gate(app.data_root.parent))
                    ok, user = self._signed_in(url.path)
                    if not ok:
                        return
                    if user is not None:
                        role, body["reviewer"] = user["role"], user["name"]
                        events.set_actor(body["reviewer"], role)
                if url.path == "/api/retention":
                    from portal.communication_consent import data_gate
                    destruction_case = str(body.get("case") or "")
                    if not app.may_open(user, destruction_case):
                        return self._json(UNKNOWN, 404)
                    stack.enter_context(data_gate(app.data_root.parent))
                    stack.enter_context(app.case_write(destruction_case, url.path))
                    ok, user = self._signed_in(url.path)
                    if not ok:
                        return
                    if not app.may_open(user, destruction_case):
                        return self._json(UNKNOWN, 404)
                    if user is not None:
                        role, body["reviewer"] = user["role"], user["name"]
                        events.set_actor(body["reviewer"], role)
                if url.path in CASE_POST and not app.may_open(user, str(client or "")):
                    # a case this person may not open, or no case at all (src/restricted.py): one answer for both
                    return self._json(UNKNOWN, 404)
                if url.path in CASE_POST:  # in turn with the worker's reading and the night's run (an added scan only lands in the folder: it waits for nobody)
                    # Family pair mutations take both locks in stable order inside
                    # their helper; taking the source first here would invert it.
                    if not (url.path == "/api/journey" and body.get("action") in ("link", "unlink")):
                        stack.enter_context(app.case_write(client, url.path))
                    if url.path in GATED_POST:
                        # Waiting for the installation/case gates must not keep
                        # a stale actor or ACL alive through a legal decision.
                        ok, user = self._signed_in(url.path)
                        if not ok:
                            return
                        if not app.may_open(user, str(client or "")):
                            return self._json(UNKNOWN, 404)
                        if user is not None:
                            role, body["reviewer"] = user["role"], user["name"]
                            events.set_actor(body["reviewer"], role)
                    self._touch_case = str(client or "")
                self._touch_all = not FIRM_WIDE.isdisjoint((urlparse(self.path).path,))  # a change to the firm's settings, policies or approvals alters every row
                if url.path == "/api/support":  # Let support in, or end a support session (the attorney's)
                    self._support_post(body, user)
                elif url.path == "/api/decide":
                    self._json(app.decide(client, body, role))
                elif url.path == "/api/assignment":
                    self._json(app.assignment_change(client, body, user))
                elif url.path == "/api/undo":
                    self._json(app.undo(client, body, role))
                elif url.path == "/api/apply":
                    self._json(app.apply(client))
                elif url.path == "/api/filed":
                    self._json(app.set_filed(client, body, role))
                elif url.path == "/api/packet":
                    self._json(app.build_packet(client, body))
                elif url.path == "/api/packet-file":
                    self._json(app.packet_choice(client, body))
                elif url.path == "/api/document":  # whose a document is, its quality, a role tag (src/documents.py)
                    self._json(app.document_change(client, body, role))
                elif url.path == "/api/absence":  # the client has no such document, or the mark taken off (src/absence.py)
                    self._json(app.absence_change(client, body, role))
                elif url.path == "/api/translation":  # make, correct, choose the translator, sign (src/translation.py)
                    self._json(app.translation_change(client, body, role))
                elif url.path == "/api/declaration":  # the client's declaration: English, edits, final, signed (src/drafting.py)
                    self._json(app.declaration_change(client, body, role))
                elif url.path == "/api/case-question":  # Ask about this case: answered from its own record, every sentence cited
                    self._json(app.case_question(client, body, role))
                elif url.path == "/api/case-summary":  # the summary for the attorney, built the same way
                    self._json(app.case_summary(client, body, role))
                elif url.path == "/api/filing-mode":
                    self._json(app.filing_mode(client, body))
                elif url.path == "/api/online-bundle":
                    self._json(app.build_online_bundle(client, body))
                elif url.path == "/api/review-bundle":
                    self._json(app.build_review_bundle(client, body))
                elif url.path == "/api/rules/approve":
                    self._json(app.rules_approve(body, role))
                elif url.path == "/api/accuracy/mark":  # the reference was the one that was wrong: the attorney's mark (src/accuracy.py)
                    self._json(app.accuracy_mark(body, user))
                elif url.path == "/api/receipt":
                    self._json(app.add_receipt(client, body))
                elif url.path == "/api/i360":
                    self._json(app.i360_answer(client, body, role))
                elif url.path == "/api/family":
                    self._json(app.family_answer(client, body, role))
                elif url.path == "/api/journey":
                    self._json(app.journey_mark(client, body, role, user))
                elif url.path == "/api/family-link-recovery":
                    self._json(app.family_recovery(client, user, body))
                elif url.path == "/api/path":  # a change to the case's path, its approval, its refusal, or back to the template (src/path.py)
                    self._json(app.path_change(client, body, user))
                elif url.path == "/api/clio-send":  # "Send now" for this case: its packet, stage, deadlines, tasks and end state to its Clio matter
                    self._json(app.clio_send(client, body, role))
                elif url.path == "/api/engagement-preview":
                    self._json(app.engagement_preview(client, body))
                elif url.path == "/api/engagement":  # the agreement, its signing, and the case's end (src/engagement.py)
                    self._json(app.engagement_change(client, body, role, session_token=self._token()))
                elif url.path == "/api/engagement-paper":  # the agreement the client signed on paper: the scan and the date
                    self._json(app.engagement_paper(client, body, role))
                elif url.path == "/api/eoir26a":  # the fee waiver request: send, type a figure, item 4, open the signing, attest (src/eoir26a.py)
                    self._json(app.eoir26a_change(client, body, role))
                elif url.path == "/api/eoir26a-paper":  # the fee waiver request the client signed on paper: the scan and the date
                    self._json(app.eoir26a_paper(client, body, role))
                elif url.path == "/api/explain":  # edit, approve or take back a Part 14 explanation; the grammar helper (src/part14_explain.py)
                    self._json(app.explain_change(client, body, role, user))
                elif url.path == "/api/wordings":  # Settings, Firm wordings: approve in bulk, edit, place, set aside, retire (src/wordings.py): the attorney's
                    self._json(app.wordings_change(body, user, role))
                elif url.path == "/api/find":  # Find across the firm (src/find.py): a question; each passage gated by may_open before the ranking
                    self._json(app.find(body, user, role))
                elif url.path == "/api/purge":  # a step of the purge of one case (src/purge.py): the attorney's
                    self._json(app.purge_change(client, body, user, role, session_token=self._token()))
                elif url.path == "/api/retention-rules":  # Settings, Keeping closed files: confirm an office's rule, set the waiting period (src/purge.py)
                    self._json(app.retention_rules_change(body, user, role))
                elif url.path == "/api/firm-documents":  # Settings, Firm documents: the attorney edits one office's letter
                    self._json(app.firm_documents_save(body, user, role))
                elif url.path == "/api/retention":  # Keeping current: an attorney records a file destroyed after its keeping date
                    self._json(app.retention_mark(body, user, role, session_token=self._token()))
                elif url.path == "/api/n400":
                    self._json(app.n400_answer(client, body, role))
                elif url.path == "/api/i589":
                    self._json(app.i589_answer(client, body, role))
                elif url.path == "/api/answers":
                    self._json(app.filing_answer(client, body, role))
                elif url.path == "/api/rfe":
                    self._json(app.rfe_save(client, body))
                elif url.path == "/api/rfe-build":
                    self._json(app.rfe_build(client, body))
                elif url.path == "/api/maintenance":
                    self._json(app.maintenance_mark(body))
                elif url.path == "/api/export-firm":  # start the export of all of the firm's data (the attorney's)
                    self._json(app.export_start(user))
                elif url.path == "/api/settings":
                    self._json(app.settings_save(body, role))
                elif url.path == "/api/connections":
                    self._json(app.connections_save(body, role))
                elif url.path == "/api/office":
                    self._json(app.set_office(client, body))
                elif url.path == "/api/remind":
                    self._json(app.remind(client, body))
                elif url.path == "/api/ask":
                    self._json(app.ask_client(client, body))
                elif url.path == "/api/ask-preview":
                    self._json(app.ask_preview(client, body))
                elif url.path == "/api/ask-send":
                    self._json(app.ask_send(client, body))
                elif url.path == "/api/filing-ask":
                    self._json(app.filing_ask(client, body))
                elif url.path == "/api/message-preview":
                    self._json(app.message_preview(client, body))
                elif url.path == "/api/message-reply":
                    self._json(app.message_reply(client, body))
                elif url.path == "/api/message-done":
                    self._json(app.message_done(client, body))
                elif url.path == "/api/ask-drop":
                    self._json(app.ask_drop(client, body))
                elif url.path == "/api/request-done":
                    self._json(app.request_done(client, body))
                elif url.path == "/api/restore-drill":  # "Run it now" on the restore drill (src/backups.py drill): a job, an attorney's
                    self._json(app.restore_drill_run(user))
                elif url.path == "/api/inbox/read":  # "Read the inbox now" (src/inbox.py)
                    self._json(app.inbox_read(role, user))
                elif url.path == "/api/inbox/place":
                    self._json(app.inbox_place(body, user))
                elif url.path == "/api/inbox/not-ours":
                    self._json(app.inbox_not_ours(body, user))
                elif url.path == "/api/access":  # restrict a case, who may open it, its automatic messages (src/restricted.py)
                    self._json(app.access_change(client, body, user))
                elif url.path == "/api/staff":  # the Settings page's Staff section (review/auth.py)
                    self._json(app.staff_change(body, user))
                elif url.path == "/api/calendar":  # make or turn off the person's calendar address; reminders on or off (src/calendar_feed.py)
                    self._json(app.calendar_change(body, user))
                elif url.path == "/api/deadline":  # add a deadline, name who is responsible, mark one done (src/deadlines_set.py)
                    self._json(app.deadline_change(client, body, user))
                elif url.path == "/api/getting-started":  # "I have seen it": the page stops opening by itself for this attorney
                    self._json(app.getting_started_seen(user))
                elif url.path == "/api/client-add":  # the front desk (review/front_desk.py): never without the conflict search's decision
                    self._json(app.client_add(body, user))
                elif url.path == "/api/prospect-new":  # New prospect: the first call recorded (src/prospects.py)
                    self._json(app.prospect_new(body, user))
                elif url.path == "/api/prospect-change":  # a note, a task, an answer, send the questions, type them in, decline: gated by the prospect's own folder inside
                    self._json(app.prospect_change(body, user))
                elif url.path == "/api/g28":  # confirm the G-28 card, change a choice with a reason, or take the last change back (src/g28.py)
                    self._json({"card": app.g28_change(client, body, user)})
                elif url.path == "/api/rebuild":  # rebuild the forms with this release, or confirm the boxes that changed (src/rebuild.py)
                    self._json({"card": app.rebuild_change(client, body, user)})
                elif url.path == "/api/case-notes":  # a note or a task on the case (src/case_notes.py)
                    self._json(app.case_notes_change(client, body, user))
                elif url.path == "/api/apply-for":  # an answer to a what-could-apply-for question (src/apply_for.py)
                    self._json(app.apply_for_answer(client, body, user))
                elif url.path == "/api/conflict-search":  # the conflict search (src/conflicts.py): attorney and paralegal, each hit gated by may_open inside
                    self._json(app.conflict_search(body, user))
                elif url.path == "/api/conflict-note":  # the attorney's decision on a search by hand (Settings)
                    self._json(app.conflict_note(body, user))
                elif url.path == "/api/conflict-decide":  # the attorney's decision for a case waiting for one
                    self._json(app.conflict_decide(client, body, user))
                elif url.path == "/api/contact-recovery":
                    self._json(app.contact_recovery(body, user, change=True))
                elif url.path == "/api/promotion-recovery":
                    self._json(app.promotion_recovery(body, user, change=True))
                elif url.path == "/api/communication":
                    self._json(app.authenticated_communication_change(client, body, self._token()))
                elif url.path == "/api/prospect-communication":
                    self._json(app.authenticated_communication_change(str(body.get("prospect") or ""), body, self._token(), prospect=True))
                elif url.path == "/api/client-wording":
                    self._json(app.client_wording(body, user, change=True))
                elif url.path == "/api/client-invite":
                    self._json(app.client_invite(client, body))
                elif url.path == "/api/client-link":
                    link = app.client_link(client, body, role, user=user)
                    if self._viewed(user, client, "link"):
                        self._json(link)
                elif url.path == "/api/client-questionnaire":
                    self._json(app.client_questionnaire(client, body))
                elif url.path == "/api/drive-settings":
                    self._json(app.drive_settings_save(body, user))
                elif url.path == "/api/drive-settings-audit":
                    self._json(app.drive_settings_audit(body, user))
                elif url.path == "/api/client-upload":
                    self._json(app.client_upload(client, body, user))
                elif url.path == "/api/drive-preview":
                    self._json(app.drive_preview(client, body, user))
                elif url.path == "/api/drive-enqueue":
                    self._json(app.drive_enqueue(client, body, user))
                elif url.path == "/api/source-setup":
                    self._json(app.source_setup(client, user))
                elif url.path == "/api/policies":  # the firm's standard answers, edited by the attorney in Settings
                    self._json(app.policies_edit(body, role))
                else:
                    self._send(404, b"not found", "text/plain")
            except TimeoutError:
                self._json({"error": "The operation timed out. Reread current state before trying again.", "busy": True}, 503)
            except PermissionError as exc:
                self._json({"error": str(exc)}, 403)
            except (case_assignment.Conflict, drive_settings.Conflict, __import__("client_file_policy").Conflict) as exc:
                self._json({"error": str(exc), "conflict": True}, 409)
            except case_assignment.Unavailable as exc:
                self._json({"error": str(exc)}, 503)
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            except LookupError as exc:
                self._json({"error": str(exc)}, 404)
            except jobs.CaseBusy as exc:  # the case is being read: the screen says so and the person tries again
                self._json({"error": str(exc)}, 409)
            except Exception:  # noqa: BLE001 -- a body of the wrong shape, or a bug: said on the console; the person gets a 500 in words
                self._failed()
            finally:
                stack.close()
                if url.path in CASE_POST and isinstance(body, dict) and isinstance(body.get("client"), str):
                    app.roster.touch(body["client"])  # whatever it changed, the lists read this case again before the next one is built (a route that answered an error before _send ran, too)
                self._touch_case, self._touch_all = None, False
                events.clear_actor()

        def _device_cookie(self, token: str) -> tuple[str, str]:
            """"Remember this device": its own cookie, sent back only to the sign-in route, for 30 days; on the server only
            its SHA-256, under the account it was given to (review/auth.py)."""
            secure = "; Secure" if app.secure_cookies or self._tls() else ""
            return ("Set-Cookie", f"{DEVICE_COOKIE}={token}; Path=/api/login; HttpOnly; SameSite=Strict; Max-Age={int(REMEMBER.total_seconds())}{secure}")

        def _account(self, path: str, body: dict):
            """Signing in: the password (/api/login, /api/password on the first sign-in), then the code when one is owed
            (/api/code), or setting the app up first (/api/enrol: without a code, the secret to scan; with one, done). A step
            that ran out sends the person back to the password (401, sign_in)."""
            from review.auth import second_factor

            try:
                if path == "/api/code":
                    done = app.accounts.verify_code(self._token(), str(body.get("code") or ""), bool(body.get("remember")))
                    headers = [tuple(self._cookie(done["token"]).items())[0]] + ([self._device_cookie(done["device"])] if done["device"] else [])
                    return self._json({"user": done["user"], "recovery_left": done["recovery_left"]}, extra=headers)
                if path == "/api/enrol":
                    if not str(body.get("code") or "").strip():
                        shown = app.accounts.enrol_start(self._token(), app.code_issuer())
                        return self._json({"letters": shown["letters"], "account": shown["account"], "qr": qr.svg(shown["uri"])})
                    done = app.accounts.enrol_finish(self._token(), str(body.get("code") or ""))
                    return self._json({"user": done["user"], "recovery_codes": done["recovery_codes"]}, extra=self._cookie(done["token"]))
            except LookupError as exc:  # the step ran out (or was never started here): back to the password
                return self._json({"error": str(exc), "sign_in": True}, 401)
            email = str(body.get("email") or "")
            if path == "/api/password":
                app.accounts.change_password(email, str(body.get("password") or ""), str(body.get("new_password") or ""))
            token, user = app.accounts.sign_in(email, str(body.get("new_password") or body.get("password") or ""), self._token(DEVICE_COOKIE))
            out = {"user": user} | ({"remember_allowed": second_factor()["remember"], "issuer": app.code_issuer()}
                                    if user.get("second_factor") == "code" else {})
            self._json(out, extra=self._cookie(token))

    return Handler


def local_setup_allowed(host: str, hostnames: tuple[str, ...] = ()) -> bool:
    """Whether "Set up the first attorney" is shown to "the computer itself" with a box for the code (the code is needed either way): only an
    app that listens on this computer alone and is reached by no name. One that serves the network, or is given a name staff type (--hostname), sits behind a proxy or
    is reached from other computers, and a proxy that passes the request on with the Host of its own address and no forwarding header
    would make a stranger look local: there only the one-time code works."""
    return host in ("127.0.0.1", "localhost", "::1") and not hostnames


def serve(app: ReviewApp, port: int, host: str = "127.0.0.1", hostnames: tuple[str, ...] = ()) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1") and app.accounts is None:
        raise SystemExit("Serving beyond this machine needs staff accounts: create them with src/review/users.py first.")
    return ThreadingHTTPServer((host, port), make_handler(app, port, hostnames))


def main(argv: list[str] | None = None) -> None:
    repo = Path(__file__).resolve().parents[2]
    args = staff_arguments(argv, repo=repo)
    app, accounts = build_staff_app(args, accounts_factory=Accounts, app_factory=ReviewApp,
                                    local_setup=local_setup_allowed(args.host, tuple(args.hostname)))
    # The offline translator takes ~20 s to load; do it now, in the background,
    # instead of on the first client whose questionnaire needs a translation.
    from classify.translate import warm_up

    threading.Thread(target=warm_up, daemon=True, name="translator-warm-up").start()
    server = serve(app, args.port, args.host, tuple(args.hostname))
    jobs.sweep(args.data, args.portal)  # a reading a stopped worker left says "Did not finish: will be read tonight", and its photos are for tonight
    jobs.ensure_worker(args.data, args.portal)  # no worker registered (a laptop with only the app): the app starts its own
    threading.Thread(target=app.warm_up, daemon=True, name="warm-up").start()  # the lists and the logs' indexes, read before the first person asks
    app.clio_resume()  # uploads Clio's webhooks named before a restart are read within the hour
    app.start_posture()  # the computer's own duties (disk, screen, backup, updates, firewall) read in the background and kept (src/posture.py)
    if accounts is not None and accounts.needs_setup():  # the setup page's own second factor: a code shown only to whoever can see this window
        code = accounts.console_setup_code()
        where = f"http://{args.hostname[0] if args.hostname else '127.0.0.1'}:{args.port}/"
        print(f"No staff account yet. To set up the first attorney, open {where}?setup={code} (behind the firm's HTTPS proxy, its own address with "
              f"?setup={code}). This code works once, for 14 days; a new one is printed each time the app starts while nobody has an account, and the "
              "installer's code still works too.", flush=True)  # a window written to a log file (the installer's unit) has it at once
    who = f"sign-in required ({len(accounts.users())} accounts)" if accounts else "no accounts: this machine only"
    print(f"Review app on http://{'127.0.0.1' if args.host in ('127.0.0.1', 'localhost') else args.host}:{args.port}  ({who}; Ctrl+C to stop)")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not (args.behind_tls_proxy or args.trusted_proxy or args.secure_cookies):
        print("Warning: serving the network over plain HTTP. Put the firm's HTTPS proxy in front (docs/hardening.md).")
    server.serve_forever()


if __name__ == "__main__":
    main()
