"""The client portal: the firm's own intake questionnaire on the client's
phone. Internet-facing (behind HTTPS on firm-owned hosting), so:

  - sign-in by one-time link (texted/emailed); session in an HttpOnly,
    Secure, SameSite=Strict cookie; nothing to remember, nothing to phish
    for a password;
  - every write needs the X-Portal header (a cross-site form can't send it)
    and a session; a client only ever reaches their own folder;
  - "send me a link" answers the same whether or not the contact is known;
    sign-in attempts are rate-limited per address. Behind a reverse proxy
    every client arrives from the proxy's address, so PORTAL_TRUSTED_PROXY
    (the proxy's address, several separated by commas, or "1" for whoever
    connects) makes the first address in X-Forwarded-For the client's;
    without it that header is ignored (anyone can write it). The proxy must
    replace the header, not add to it (docs/hardening.md);
  - uploads: PDF/JPEG/PNG only, 15 MB max, stored under random names;
  - strict security headers; the page loads nothing from other origins;
  - nothing here runs the pipeline -- the firm's machine pulls the queue
    (src/portal/engine.py).

    uvicorn portal.app:app --app-dir src --port 8600      (TLS terminates at the reverse proxy)
"""

from __future__ import annotations

import os
import re
import secrets
import time
from collections import defaultdict, deque
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from .bank import (all_questions, bank_for, clean, languages, load_help, localized, localized_faq, localized_scan_guide,
                   missing_required, required_documents, visible)
from .messages import thread_for_client
from .notify import Notifier, cases_folder, firm_name, prospects_folder
from .questions import clean_reply, for_client
from .store import MAX_UPLOAD, PortalStore, file_kind

import clock
import eoir26a
import prospects

REPO = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"
EXAMPLES = STATIC / "examples"  # what each document looks like: sketches, or the firm's own redacted images (same name, .png/.jpg wins)
MAX_MESSAGE = 2000  # characters in one message to the office
COOKIE = "portal_session"
# Request bodies (bytes), refused with 413 before they are read: an upload is one file of up to MAX_UPLOAD (plus its form's wrapping); every other write
# is a small JSON body (the largest, a drawn signature, is under 410 KB: src/eoir26a.py MAX_DRAWING). A body sent without a length (chunked) gets 411.
MAX_BODY = 1024 * 1024
MAX_BODY_UPLOAD = MAX_UPLOAD + 1024 * 1024
# Per address (PORTAL_TRUSTED_PROXY's X-Forwarded-For, else the connection) on the routes anyone can reach: a sign-in link tried (per hour), "send me a
# link" (per hour, beside the 5 an hour per contact: 120, so a workshop or a clinic on one Wi-Fi gets through, while one address cannot text every client
# it can guess), and the page, its sketches and /api/me without a session (per minute: a flood, not a guess). The page says a 429 in the client's words.
LIMITS = {"link": (30, 3600), "ask_address": (120, 3600), "open": (300, 60)}
# What a phone opening a sign-in link or the page is shown when its network went over its allowance (429): a page of its own in the four languages, never
# the raw refusal. DRAFT for the attorney and a certified translator (docs/attorney_review.md); the Haitian Creole is a machine draft. No script, no style.
BUSY_PAGE = """<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>429</title></head><body>
<p lang="pt">Muitas tentativas desta rede agora. Tente de novo mais tarde, ou ligue para o escritório.</p>
<p lang="es">Demasiados intentos desde esta red ahora. Intente de nuevo más tarde, o llame a la oficina.</p>
<p lang="en">Too many tries from this network right now. Try again later, or call the office.</p>
<p lang="ht">Twòp esè soti nan rezo sa a kounye a. Eseye ankò pita, oswa rele biwo a.</p>
</body></html>
"""
# The phone's camera for a photo of a document (the page may ask); never the microphone or the location.
PERMISSIONS = "camera=(self), microphone=(), geolocation=(), payment=(), usb=()"


def page_csp(nonce: str) -> str:
    """The page's Content-Security-Policy: its one script and its one style block run with this answer's nonce only ('unsafe-inline' is gone for
    scripts). Inline style *attributes* stay allowed (style-src-attr: the page sets style="..." on elements it builds, which a nonce cannot cover); a
    browser too old for style-src-elem and style-src-attr falls back to style-src, which keeps 'unsafe-inline' for styles only. Images: the page's own
    and the photo previews the phone draws (data:, blob:)."""
    return (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; style-src-elem 'self' 'nonce-{nonce}'; "
            "style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; form-action 'self'; base-uri 'none'; "
            "frame-ancestors 'none'")


def security_headers(content_type: str, static: bool = False) -> dict[str, str]:
    """What every answer of the portal carries (the page's own Content-Security-Policy replaces the one here). A PDF (the preparation sheet) is
    only kept out of frames: the phone's own viewer shows it, and some viewers stop under default-src 'none'. The sketches of what a document
    looks like (/examples/) are the only files a cache may keep (static: no client's data in them); everything else is no-store."""
    kind = content_type.split(";")[0].strip().lower()
    policy = ("frame-ancestors 'none'; base-uri 'none'; form-action 'none'" if kind == "application/pdf"
              else "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    return {"Content-Security-Policy": policy, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Cache-Control": "public, max-age=3600" if static else "no-store", "Permissions-Policy": PERMISSIONS,
            "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Resource-Policy": "same-origin"}


def _in_language(task: dict[str, Any], lang: str) -> dict[str, Any]:
    """A task in the language the client chose now (engine._texts), not the one it was made in."""
    return task | {"text": (task.get("texts") or {}).get(lang, task.get("text")),
                   **({"labels": task["labels_by_lang"][lang]} if lang in (task.get("labels_by_lang") or {}) else {})}


def sent_to_office(requests: list[dict[str, Any]], lang: str, bank: dict[str, Any]) -> list[dict[str, Any]]:
    """What the client answered or sent for the office's questions that the office has not marked done: the portal says "Sent to the
    office on <day>" for each, in place of the answer box. day: the office's date (src/clock.py) as YYYY-MM-DD, written in the client's
    words by the page."""
    import client_case

    week = client_case.stale_answers(requests)  # an answer with no review card behind it that nobody has marked done for seven days: "The office has your answer"
    out = []
    for r in requests:
        if r["status"] == "answered" and not r.get("settled_at"):
            day = clock.local_date(r.get("answered_at"))
            out.append({"id": r["id"], "text": for_client(r, lang, bank)["text"], "on": day.isoformat() if day else None, "doc": bool(r.get("doc_id")),
                        "kept": r["id"] in week, "kept_text": client_case.label("answer_kept", lang) if r["id"] in week else None})
    return out


def create_app(root: str | Path | None = None, base_url: str | None = None, secure_cookies: bool | None = None,
               notifier: Notifier | None = None, trusted_proxy: str | None = None) -> FastAPI:
    store = PortalStore(root or os.environ.get("PORTAL_DATA", REPO / "data" / "portal"))
    client_store = store
    trusted = {a.strip() for a in (os.environ.get("PORTAL_TRUSTED_PROXY", "") if trusted_proxy is None else trusted_proxy).split(",") if a.strip()}

    def address(request: Request) -> str:
        """The client's address: the first in X-Forwarded-For when the request came from the trusted proxy, else the connection's."""
        peer = request.client.host if request.client else "?"
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return forwarded if forwarded and ("1" in trusted or peer in trusted) else peer

    base_url = (base_url or os.environ.get("PORTAL_BASE_URL", "http://localhost:8600")).rstrip("/")
    secure = base_url.startswith("https://") if secure_cookies is None else secure_cookies
    notifier = notifier or Notifier(store.root / "outbox.jsonl", store=store)
    notifier.env["PORTAL_BASE_URL"] = base_url
    # People who are not clients yet (src/prospects.py): the same sign-in links and the same questionnaire machinery, in a store of their own (portal/prospects), so a prospect is
    # never among the clients. Their messages are checked against the prospects' folder: a prospect of a protected kind is restricted there, like a case.
    pstore = prospects.store(store.root)
    pnotifier = Notifier(store.root / "outbox.jsonl", cases_root=prospects_folder(), store=pstore)
    pnotifier.env["PORTAL_BASE_URL"] = base_url
    attempts: dict[str, deque] = defaultdict(deque)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.notifier, app.state.base_url = store, notifier, base_url

    def limited(key: str, per_hour: int, seconds: int = 3600) -> bool:
        """One more attempt under this key; True when it went over per_hour in the last `seconds` (an hour unless said). Quiet keys are forgotten when
        there are many (a flood of made-up contacts must not fill the memory)."""
        now = time.time()
        if len(attempts) > 20000:
            for k in [k for k, w in attempts.items() if not w or w[-1] < now - 3600]:
                del attempts[k]
        window = attempts[key]
        while window and window[0] < now - seconds:
            window.popleft()
        window.append(now)
        return len(window) > per_hour

    def limited_address(kind: str, request: Request) -> bool:
        per, seconds = LIMITS[kind]
        return limited(f"{kind}:{address(request)}", per, seconds)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        """Every answer, errors included (403, 404, 413, 429, 500), carries the same headers (security_headers); a page's Content-Security-Policy is
        its own (page(): the nonce). An error nobody expected is said on the console and answered 500 in a word, with the headers."""
        length = (request.headers.get("content-length") or "").strip()
        cap = MAX_BODY_UPLOAD if request.url.path == "/api/upload" else MAX_BODY
        stop_hook = request.method == "POST" and request.url.path == "/api/communication/stop/twilio"
        if request.method in ("POST", "PUT", "DELETE") and request.headers.get("X-Portal") != "1" and not stop_hook:
            response: Response = JSONResponse({"error": "forbidden"}, status_code=403)
        elif request.method in ("POST", "PUT", "PATCH") and not length and request.headers.get("transfer-encoding"):
            response = JSONResponse({"error": "length_required"}, status_code=411)  # a body with no length can't be measured before it is read
        elif length and (not (length.isascii() and length.isdigit()) or int(length) > cap):
            response = JSONResponse({"error": "too_large"}, status_code=413)  # refused before a byte of it is read
        else:
            try:
                response = await call_next(request)
            except TimeoutError:
                response = JSONResponse({"error": "server_busy", "retryable": True}, status_code=503)
            except Exception:  # noqa: BLE001 -- the detail on the console, never on the client's phone
                import traceback

                traceback.print_exc()
                response = JSONResponse({"error": "server_error"}, status_code=500)
        static = request.url.path.startswith("/examples/") and response.status_code == 200
        for k, v in security_headers(response.headers.get("content-type") or "", static).items():
            if k == "Content-Security-Policy" and k in response.headers:
                continue  # the page's own, with its nonce
            response.headers[k] = v
        if secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    def client_of(request: Request) -> str:
        client_id = store.session_client(request.cookies.get(COOKIE))
        if client_id is None:
            raise HTTPException(401, "sign in again")
        return client_id

    def who_of(request: Request) -> tuple[PortalStore, str]:
        """The signed-in person and the store that holds them: a client's, or (a person who is not a client yet) a prospect's. Only the questionnaire's own routes use this;
        every other route is a client's and answers 401 to a prospect."""
        cookie = request.cookies.get(COOKIE)
        client_id = store.session_client(cookie)
        if client_id is not None:
            return store, client_id
        prospect_id = pstore.session_client(cookie)
        if prospect_id is None:
            raise HTTPException(401, "sign in again")
        return pstore, prospect_id

    def open_case(client_id: str, st: PortalStore | None = None) -> str:
        """A case that ended (src/engagement.py) is read-only on the client's page: it shows the office's letter and nothing else."""
        if (st or store).engagement(client_id).get("ended"):
            raise HTTPException(409, "case_closed")
        return client_id

    def consent_store(request: Request) -> PortalStore:
        """Only an own current portal cookie can select the consent store."""
        cookie = request.cookies.get(COOKIE)
        for st in (store, pstore):
            if st.session_client(cookie, consent_only=True) is not None:
                return st
        raise HTTPException(401, "current_client_consent_access_required")

    def read_now(client_id: str, operation_id: str | None = None) -> str:
        """A new upload is written down as a job and the phone gets its answer at once; the job worker (src/jobs.py) reads it when the case is on this
        machine (portal/engine.read_new_uploads), in its own process, one job at a time and under the case's lock, so the portal never reads a document
        itself and a photo sent while the overnight run works on the case waits its turn. A case that is not here is left for the worker's next pass (the
        upload says "tonight" and the office's list repeats it). PORTAL_READ_AT_ONCE=0 turns the reading at once off (a host that must not read documents)."""
        cases = cases_folder()
        if os.environ.get("PORTAL_READ_AT_ONCE", "1") == "0" or not store.case_here(client_id, cases):
            store.mark_reading(client_id, "tonight")
            return "tonight"
        import jobs

        root = jobs.folder_for(cases)
        if operation_id:
            job = jobs.submit(root, "portal_upload", client_id, by="The client", args={"upload_operation": operation_id}, operation_id=operation_id)
            reading = "unavailable" if job["state"] == "unavailable" else "tonight" if job["state"] in ("failed", "died") else "now" if jobs.alive(root) else "tonight"
        else:
            jobs.submit_once(root, "portal_upload", client_id, by="The client")
            reading = "now" if jobs.alive(root) else "tonight"
        store.mark_reading(client_id, "tonight" if reading == "unavailable" else reading)
        return reading

    # -- pages and sign-in ----------------------------------------------------------

    @app.get("/consent")
    def consent_page(request: Request):
        """Own-client service choices; this page grants no questionnaire access."""
        if limited_address("open", request):
            return Response(BUSY_PAGE, status_code=429, media_type="text/html")
        from .communication_consent import client_context
        st = consent_store(request)
        try:
            client_context(st.communication_scope(), st, request.cookies.get(COOKIE))
        except PermissionError:
            raise HTTPException(401, "current_client_consent_access_required") from None
        except (ValueError, OSError, LookupError):
            raise HTTPException(409, "current_reviewed_notice_unavailable") from None
        nonce = secrets.token_urlsafe(18)
        html = (STATIC / "consent.html").read_text(encoding="utf-8").replace('nonce="%%NONCE%%"', f'nonce="{nonce}"')
        return Response(html, media_type="text/html", headers={"Content-Security-Policy": page_csp(nonce)})

    @app.get("/api/communication/consent")
    async def consent_notice(request: Request):
        from .communication_consent import client_context
        st = consent_store(request)
        try:
            return client_context(st.communication_scope(), st, request.cookies.get(COOKIE))
        except PermissionError:
            raise HTTPException(401, "current_client_consent_access_required") from None
        except (ValueError, OSError, LookupError):
            raise HTTPException(409, "current_reviewed_notice_unavailable") from None

    @app.post("/api/communication/consent")
    async def consent_signoff(request: Request):
        from .communication_consent import client_grant
        st = consent_store(request)
        try:
            return client_grant(st.communication_scope(), st, request.cookies.get(COOKIE), await request.json())
        except PermissionError:
            raise HTTPException(401, "current_client_consent_access_required") from None
        except (ValueError, OSError, LookupError):
            raise HTTPException(409, "explicit_current_service_signoff_required") from None

    @app.post("/api/communication/revoke")
    async def consent_revoke(request: Request):
        from .communication_consent import client_revoke
        st = consent_store(request)
        try:
            return client_revoke(st.communication_scope(), st, request.cookies.get(COOKIE), await request.json())
        except PermissionError:
            raise HTTPException(401, "current_client_consent_access_required") from None
        except (ValueError, OSError, LookupError):
            raise HTTPException(409, "service_revocation_unavailable") from None

    @app.post("/api/communication/stop/twilio")
    async def stop_twilio(request: Request):
        """SDK-authenticated, durable inbound STOP; never an outbound reply."""
        from .opt_out import ingest
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/x-www-form-urlencoded":
            raise HTTPException(415, "unsupported_webhook_body")
        try:
            outcome = ingest(store.communication_scope(), await request.body(), request.headers.get("X-Twilio-Signature", ""), env=dict(os.environ))
        except PermissionError:
            raise HTTPException(403, "webhook_authentication_failed") from None
        except (ValueError, OSError, ImportError, TimeoutError):
            raise HTTPException(503, "webhook_not_retained_retry_required") from None
        return Response("<Response/>", media_type="application/xml", headers={"X-Communication-State": outcome["state"]})

    @app.get("/")
    def page(request: Request):
        """The page, with the firm's name (Settings, else the built-in name) in the title and the top bar."""
        if limited_address("open", request):
            return Response(BUSY_PAGE, status_code=429, media_type="text/html")  # a person's browser: words, not JSON
        name = firm_name()
        import settings

        nonce = secrets.token_urlsafe(18)  # the page's one script and one style block run with this answer's nonce only
        html = (STATIC / "portal.html").read_text(encoding="utf-8").replace("%%FIRM%%", escape(name)).replace("%%MARK%%", escape(settings.firm_mark(name))) \
            .replace('nonce="%%NONCE%%"', f'nonce="{nonce}"').replace("/* %%CAPTURE_VISION%% */", (STATIC / "capture.js").read_text(encoding="utf-8")) \
            .replace("/* %%CAPTURE_UI%% */", (STATIC / "capture-ui.js").read_text(encoding="utf-8"))
        return Response(html, media_type="text/html", headers={"Content-Security-Policy": page_csp(nonce)})

    @app.get("/examples/{name}")
    def example(name: str, request: Request):
        if limited_address("open", request):
            raise HTTPException(429, "too many requests")
        if not re.fullmatch(r"[a-z0-9_]+\.(svg|png|jpg)", name) or not (EXAMPLES / name).is_file():
            raise HTTPException(404, "not found")
        return FileResponse(EXAMPLES / name)

    def example_for(doc_id: str) -> str | None:
        for ext in ("png", "jpg", "svg"):
            if (EXAMPLES / f"{doc_id}.{ext}").is_file():
                return f"/examples/{doc_id}.{ext}"
        return None

    @app.get("/l/{token}")
    def sign_in(token: str, request: Request):
        if limited_address("link", request):
            return Response(BUSY_PAGE, status_code=429, media_type="text/html")  # the client tapped a link: words, not JSON
        from .communication_consent import gate
        from .store import _hash
        # Preview/security GETs must never consume staff handover credentials.
        # A deliberate button click redeems them through the protected POST.
        for st in (store, pstore):
            with gate(st.communication_scope()), st._lock:
                entry = st._auth()["links"].get(_hash(token))
                if isinstance(entry, dict) and entry.get("purpose") == "staff_questionnaire":
                    nonce = secrets.token_urlsafe(18)
                    html = (STATIC / "access.html").read_text(encoding="utf-8").replace("%%NONCE%%", nonce)
                    return Response(html, media_type="text/html", headers={"Content-Security-Policy": page_csp(nonce), "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        session = store.redeem_link(token) or pstore.redeem_link(token)  # a client's link, else a prospect's
        consent_only = session and not any(st.session_client(session) is not None for st in (store, pstore))
        response = RedirectResponse(("/consent" if consent_only else "/") if session else "/?expired=1", status_code=303)
        if session:
            response.set_cookie(COOKIE, session, httponly=True, secure=secure, samesite="strict", max_age=12 * 3600)
        return response

    @app.post("/api/access-link")
    async def redeem_staff_link(request: Request):
        if limited_address("link", request):
            raise HTTPException(429, "too many attempts")
        body = await request.json()
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            raise HTTPException(400, "invalid link")
        from .communication_consent import gate
        from .store import _hash
        session = None
        for st in (store, pstore):
            with gate(st.communication_scope()), st._lock:
                entry = st._auth()["links"].get(_hash(token))
                if isinstance(entry, dict) and entry.get("purpose") == "staff_questionnaire":
                    session = st.redeem_link(token)
                    break
        if not session:
            raise HTTPException(401, "link expired or already used")
        response = JSONResponse({"redirect": "/"})
        response.set_cookie(COOKIE, session, httponly=True, secure=secure, samesite="strict", max_age=12 * 3600)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/link")
    async def request_link(request: Request):
        if limited_address("ask_address", request):  # many contacts from one address: someone trying who is a client, or sending texts to people
            raise HTTPException(429, "too many attempts")
        try:
            body = await request.json()
        except ValueError:
            body = None
        from .contact_access import lookup_contact, rate_identity
        from .communication_consent import gate
        contact = body.get("contact", "") if isinstance(body, dict) else ""
        hinted = isinstance(body, dict) and "channel" in body
        channel_hint = body.get("channel") if hinted else None
        valid_hint = not hinted or isinstance(channel_hint, str) and channel_hint in {"sms", "whatsapp"}
        if not limited(f"ask:{rate_identity(contact)}", 5):
            try:
                scope = store.communication_scope()
                with gate(scope):
                    match = lookup_contact(scope, contact)
                    if match["matched"] and valid_hint and (not hinted or match["channel"] == "sms"):
                        st, sender = (store, notifier) if match["store_kind"] == "client" else (pstore, pnotifier)
                        client_id = match["client"]
                        if not st.stopped(client_id):
                            st.log(client_id, "link_requested")
                            # The supplied destination selects only that channel;
                            # consent/STOP/readiness and pending transitions still
                            # decide dispatch before creating any credential.
                            selected_channel = channel_hint if hinted else match["channel"]
                            sender.send(st.profile(client_id), "invite", skip_channels=tuple(c for c in ("email", "sms", "whatsapp") if c != selected_channel))
            except (ValueError, LookupError, OSError, TimeoutError, TypeError):
                pass  # malformed/unavailable installation has the same public response
        return {"ok": True}  # generic public response, no identity fields; timing is not uniform

    @app.post("/api/logout")
    def logout(request: Request):
        store.end_session(request.cookies.get(COOKIE))
        pstore.end_session(request.cookies.get(COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE)
        return response

    # -- the questionnaire -------------------------------------------------------------

    def agreement_view(client_id: str, lang: str, st: PortalStore | None = None) -> dict[str, Any] | None:
        """The agreement on the client's page: the letter in their language (when it was made in it) and in English, and whether it is signed."""
        e = (st or store).engagement(client_id)
        letter = ((e.get("signed") or {}).get("letter_snapshot") or e.get("letter"))
        if not letter:
            return None
        signed = e.get("signed")
        return {"id": letter["id"], "titles": letter.get("titles") or {}, "texts": letter.get("texts") or {}, "date": letter.get("date"),
                "language": lang if lang in (letter.get("texts") or {}) else "en", "translation": letter.get("translation"),
                "signed_on": clock.local_date(signed["at"]).isoformat() if signed and clock.local_date(signed.get("at")) else None,
                "consent": letter.get("consent") if (letter.get("consent") or {}).get("language") == (lang if lang in (letter.get("texts") or {}) else "en") else None,
                "evidence": __import__("signing_evidence").verify(Path(cases_folder()) / client_id, letter["id"]) if signed and signed.get("how") == "portal" else None}

    def feedback_view(client_id: str, profile: dict[str, Any], lang: str, store: PortalStore) -> dict[str, Any] | None:
        """The one step to ask "how was this step?" about now (src/client_case.py): the firm's milestones in the client's page, and the two the portal
        knows itself (the agreement signed, the documents sent). store: the one that holds this person (a prospect's, src/prospects.py, has none to ask about)."""
        import client_case

        views = store.journey(client_id) or {}
        mine = list((views.get(lang) or views.get("en") or {}).get("milestones") or [])  # the same in every language
        signed = (store.engagement(client_id).get("signed") or {}).get("at")
        if signed and clock.local_date(signed):
            mine.append({"id": "agreement", "kind": "agreement", "date": clock.local_date(signed).isoformat()})
        sent = profile.get("submitted_at")
        if sent and clock.local_date(sent):
            mine.append({"id": "documents", "kind": "documents", "date": clock.local_date(sent).isoformat()})
        return client_case.feedback_prompt(mine, {r.get("step") for r in store.feedback(client_id)}, clock.today(), lang)

    def office_has_none(client_id: str) -> list[str]:
        """Fictional example or implementation helper."""
        import absence

        case = cases_folder() / client_id
        try:
            return sorted(absence.absent_types(case)) if case.is_dir() else []
        except Exception:  # noqa: BLE001 -- a case that cannot be read here: the list stays as it is
            return []

    def state(client_id: str, st: PortalStore | None = None) -> dict[str, Any]:
        store = st or client_store  # st: a prospect's store (src/prospects.py); a client's by default (this function's "store" is the one it is asked about)
        from .display_profile import current
        profile, answers = current(store, client_id), store.answers(client_id)
        ended = store.engagement(client_id).get("ended")
        if ended:  # the case with the office ended: its letter, and nothing else (src/engagement.py)
            lang = profile.get("language", "pt")
            letter = ended.get("letter") or {}
            return {"first_name": (profile.get("name") or "").split(" ")[0], "language": lang, "status": profile.get("status"), "filing": profile.get("filing"),
                    "closed": {"since": ended.get("since"), "state": ended.get("state"),
                               "letter": {"titles": letter.get("titles") or {}, "texts": letter.get("texts") or {},
                                          "language": lang if lang in (letter.get("texts") or {}) else "en"} if letter else None},
                    "progress": 100, "messages": [], "faq": localized_faq(lang),
                    "agreement": agreement_view(client_id, lang, store), "file_sent": bool(store.engagement(client_id).get("file_sent"))}
        bank = bank_for(profile)  # the client's filing decides the questions (green card, citizenship)
        lang = profile.get("language", "pt")
        docs = required_documents(answers, bank)
        asked = {r["doc_id"] for r in store.client_requests(client_id) if r["status"] == "open" and r.get("doc_id")}
        docs += [d | {"required": True, "count": 1} for d in bank["documents"] if d["id"] in asked and d["id"] not in {x["id"] for x in docs}]
        uploads = store.uploads(client_id)
        import client_case

        closed = {t["doc_id"] for t in store.tasks(client_id) if t.get("office_has")}  # papers the office recorded the client has none of: "The office has what it needs"
        doc_tips = load_help().get("documents", {})
        doc_view = [{"id": d["id"], "label": d["label"].get(lang) or d["label"]["en"], "why": d["why"].get(lang) or d["why"]["en"],
                     "tip": (doc_tips.get(d["id"]) or {}).get(lang) or (doc_tips.get(d["id"]) or {}).get("en"), "example": example_for(d["id"]),
                     "required": d["required"] and d["id"] not in closed, "count": d["count"], **({"office_has": client_case.label("office_has", lang)} if d["id"] in closed else {}), "translation": d.get("needs_translation", False),
                     "uploads": [{"id": u["id"], "name": u["filename"], "status": u.get("status")} for u in uploads if u["doc_id"] == d["id"]]}
                    for d in docs]
        total = [q for q in all_questions(bank).values() if q.get("required", True) and visible(q, answers)]
        missing = missing_required(bank, answers)
        from .bank import answer_checks
        fee_waiver = store.fee_waiver(client_id)
        return {"first_name": (profile.get("name") or "").split(" ")[0], "language": lang, "status": profile.get("status"), "filing": profile.get("filing"),
                "money_locked": bool(fee_waiver.get("signing") or fee_waiver.get("signed")),
                "answer_checks": answer_checks(bank, answers),
                "sections": localized(bank, lang, answers), "answers": answers, "documents": doc_view, "office_has_none": office_has_none(client_id),
                "tasks": [for_client(r, lang, bank)  # the office's questions, in the client's language when the office translated them
                          for r in store.client_requests(client_id) if r["status"] == "open"] + [_in_language(t, lang) for t in store.tasks(client_id)], "missing": missing, "faq": localized_faq(lang), "scan_guide": localized_scan_guide(lang),
                "sent": sent_to_office(store.client_requests(client_id), lang, bank),  # answered, and the office has not marked it done yet
                "submitted_at": profile.get("submitted_at"),
                "office_time_zone": clock.zone_name(),  # the portal says the office's zone when the client's phone is in another
                "messages": thread_for_client(store.messages(client_id), lang),  # the thread with the office, in the client's language
                "journey": (store.journey(client_id) or {}).get(lang),  # src/journey.py: the stage, what happens now, appointments
                "file_sent": bool(store.engagement(client_id).get("file_sent")),
                "agreement": agreement_view(client_id, lang, store),  # the agreement to sign, or signed (src/engagement.py)
                "money": eoir26a.client_view(store, client_id, lang),  # "Your monthly money" and its signing, when the office sent it (src/eoir26a.py)
                "feedback": feedback_view(client_id, profile, lang, store),  # "How was this step?" after a milestone, or None
                "progress": round(100 * (len(total) - len(missing)) / max(1, len(total)))}

    @app.get("/api/me")
    def me(request: Request):
        from .communication_consent import closed_projection
        cookie = request.cookies.get(COOKIE)
        for st in (store, pstore):
            if st.session_client(cookie, closed_artifacts=True) is not None:
                try:
                    return closed_projection(st.communication_scope(), st, cookie)
                except (PermissionError, ValueError, OSError, LookupError):
                    raise HTTPException(401, "closed_artifact_unavailable") from None
        try:
            st, client_id = who_of(request)
        except HTTPException:
            if limited_address("open", request):  # asked again and again without a session: a flood
                raise HTTPException(429, "too many requests") from None
            raise
        return state(client_id, st)

    @app.get("/api/communication/closed-letter")
    def closed_letter(request: Request):
        from .communication_consent import closed_projection
        cookie = request.cookies.get(COOKIE)
        for st in (store, pstore):
            if st.session_client(cookie, closed_artifacts=True) is not None:
                try:
                    return closed_projection(st.communication_scope(), st, cookie)["closed"]["letter"]
                except (PermissionError, ValueError, OSError, LookupError):
                    break
        raise HTTPException(401, "closed_artifact_unavailable")

    @app.post("/api/language")
    async def language(request: Request):
        st, client_id = who_of(request)
        client_id, body = open_case(client_id, st), await request.json()
        if body.get("language") not in languages():
            raise HTTPException(400, "unknown language")
        st.update_profile(client_id, language=body["language"])
        st.log(client_id, "language_changed", {})  # the event ledger too (portal/store.py _LEDGER)
        return state(client_id, st)

    @app.put("/api/answers")
    async def answers(request: Request):
        st, client_id = who_of(request)
        open_case(client_id, st)
        body = await request.json()
        fee_waiver = st.fee_waiver(client_id)
        money_ids = eoir26a.questions()
        money = money_ids if fee_waiver.get("request") else {}  # only a requested waiver allows these edits after submission
        if st.profile(client_id).get("status") == "submitted" and any(q not in money for q in (body or {})):
            raise HTTPException(409, "already submitted: message the office to change an answer")
        if (fee_waiver.get("signed") or fee_waiver.get("signing")) and any(q in money_ids for q in (body or {})):
            raise HTTPException(409, "fee_waiver_locked")  # the signing is open or done: the office opens it again to change a figure
        questions = money | all_questions(bank_for(st.profile(client_id)))
        changes, errors = {}, {}
        for qid, value in (body or {}).items():
            if qid not in questions:
                continue
            cleaned, error = clean(questions[qid], value)
            if error:
                errors[qid] = error
            else:
                changes[qid] = cleaned
        if changes:
            st.save_answers(client_id, changes)
            if st.profile(client_id).get("status") == "invited":
                st.update_profile(client_id, status="started")
            if st is store:  # a prospect is not processed into a case: nothing waits in the queue for them
                st.enqueue(client_id)
        return {"errors": errors, "accepted": sorted(changes), **state(client_id, st)}

    @app.post("/api/upload")
    async def upload(request: Request, doc_id: str = Form(...), file: UploadFile = File(...), attempt: str | None = Form(None), capture_metadata: str | None = Form(None), derivative: UploadFile | None = File(None)):
        client_id = open_case(client_of(request))
        if doc_id not in {d["id"] for d in bank_for(store.profile(client_id))["documents"]}:
            raise HTTPException(400, "unknown document")
        if limited(f"upload:{client_id}", 60):
            raise HTTPException(429, "too many uploads")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file too large (15 MB max)")
        kind = file_kind(data)
        if kind is None:
            raise HTTPException(415, "PDF, JPG or PNG only")
        capture, derivative_data = None, None
        if capture_metadata is not None or derivative is not None:
            from .capture_derivatives import prepare
            derivative_data = await derivative.read(MAX_UPLOAD + 1) if derivative is not None else None
            if derivative_data is not None and len(derivative_data) > MAX_UPLOAD:
                raise HTTPException(413, "file too large (15 MB max)")
            try:
                capture = prepare(data, capture_metadata, derivative_data)
            except ValueError as exc:
                raise HTTPException(415, str(exc)) from None
        # Decode/convert before the installation writer gate. Capture evidence
        # is committed only under a fresh own-session/lifecycle authority check.
        from .store import image_to_pdf, check_pdf
        try:
            if kind == "application/pdf":
                check_pdf(data)
                converted = data
            else:
                if capture is None:
                    from .capture_derivatives import original_attachment
                    capture = original_attachment(data)
                converted = image_to_pdf(data)
        except ValueError as exc:
            raise HTTPException(415, str(exc)) from None

        def revalidate():
            if open_case(client_of(request)) != client_id:
                raise HTTPException(401, "sign in again")
            if doc_id not in {d["id"] for d in bank_for(store.profile(client_id))["documents"]}:
                raise HTTPException(409, "document_request_changed")

        with store._communication_gate(), store._lock:
            revalidate()
            if attempt is not None:
                from . import upload_recovery
                try:
                    result = upload_recovery.accept(store, client_id, attempt, doc_id, file.filename or "upload", data, kind, read_now, capture=capture, derivative=derivative_data, converted=converted)
                except ValueError as exc:
                    raise HTTPException(409 if str(exc) == "upload_attempt_conflict" else 415, str(exc)) from None
                except (OSError, TimeoutError):
                    raise HTTPException(503, "upload_incomplete") from None
                revalidate()
                return {**state(client_id), "upload_outcome": result}
            retake = any(t.get("kind") == "retake" and t.get("doc_id") == doc_id and not t.get("received_at") for t in store.tasks(client_id))
            try:
                record = store.add_upload(client_id, doc_id, file.filename or "upload", data, kind, retake=retake, capture=capture, derivative=derivative_data, converted=converted)
            except ValueError as exc:
                raise HTTPException(415, str(exc)) from None
            for r in store.client_requests(client_id):
                if r["status"] == "open" and r.get("doc_id") == doc_id:
                    store.answer_request(client_id, r["id"], upload=record["id"])
            store.mark_retakes_received(client_id, doc_id)
            store.enqueue(client_id)
            read_now(client_id)
            revalidate()
            return state(client_id)

    @app.get("/api/upload-outcome")
    def upload_outcome(request: Request, attempt: str):
        from . import upload_recovery
        with store._communication_gate(), store._lock:
            client_id = client_of(request)  # current own cookie under the repair mutation gate
            try:
                result = upload_recovery.outcome(store, client_id, attempt, read_now, repair=not store.engagement(client_id).get("ended"))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            except (OSError, TimeoutError):
                raise HTTPException(503, "upload_incomplete") from None
            if client_of(request) != client_id:
                raise HTTPException(401, "sign in again")
            return {**state(client_id), "upload_outcome": result}

    @app.post("/api/task")
    async def answer_task(request: Request):
        """A confirmation: the client picks which value is right; it becomes
        their answer (their statement, as with any other answer)."""
        client_id, body = open_case(client_of(request)), await request.json()
        task = next((t for t in store.tasks(client_id) if t["id"] == body.get("task") and t["kind"] == "confirm"), None)
        if task is None or body.get("choice") not in task["options"]:
            raise HTTPException(400, "unknown choice")
        question = all_questions(bank_for(store.profile(client_id)))[task["question"]]
        value = body["choice"]
        if question["type"] in ("choice", "yes_no"):  # the document's value is in the pipeline's form ("F"); options match it
            value = next((o["value"] for o in question.get("options", []) if o["value"] == value), value)
        cleaned, error = clean(question, value)
        if error:
            raise HTTPException(400, error)
        store.save_answers(client_id, {task["question"]: cleaned})
        confirmed = store.profile(client_id).get("confirmed", {}) | {task["question"]: cleaned}
        store.update_profile(client_id, confirmed=confirmed)  # not asked again; a remaining conflict is the paralegal's
        store.save_tasks(client_id, [t for t in store.tasks(client_id) if t["id"] != task["id"]])
        store.log(client_id, "confirmed", {"question": task["question"]})
        store.enqueue(client_id)
        return state(client_id)

    @app.post("/api/request-reply")
    async def request_reply(request: Request):
        """The client's answer to a question the office asked, typed as the question asks
        (portal/questions.py: a date as YYYY-MM-DD, Yes/No, one of the choices, or words)."""
        client_id, body = open_case(client_of(request)), await request.json()
        asked = next((r for r in store.client_requests(client_id) if r["id"] == str(body.get("request")) and r["status"] == "open"), None)
        if asked is None:
            raise HTTPException(400, "unknown request")
        reply, error = clean_reply(asked, body.get("reply"))
        if error:
            raise HTTPException(400, "write your answer first" if error == "empty" else error)
        store.answer_request(client_id, asked["id"], reply=reply)
        store.enqueue(client_id)
        return state(client_id)

    @app.post("/api/message")
    async def message(request: Request):
        """"Ask the office": the client's note, text only. It waits in the office's list (My work) and on the case's timeline."""
        client_id = open_case(client_of(request))
        try:
            body = await request.json()
        except ValueError:
            body = None
        text = str(body.get("text") or "").strip() if isinstance(body, dict) else ""
        if not text:  # the codes are worded in the client's language by the portal page (UI_MSG err_*)
            raise HTTPException(400, "empty_message")
        if len(text) > MAX_MESSAGE:
            raise HTTPException(413, "message_too_long")
        if limited(f"message:{client_id}", 20):
            raise HTTPException(429, "too_many_messages")
        store.add_message(client_id, "client", text, language=store.profile(client_id).get("language", "pt"))
        return state(client_id)

    @app.post("/api/message-seen")
    def message_seen(request: Request):
        client_id = open_case(client_of(request))
        store.mark_messages_seen(client_id)
        return state(client_id)

    @app.post("/api/feedback")
    async def give_feedback(request: Request):
        """"How was this step?": a face and an optional sentence about one step the page asked about. Kept here, copied onto the case, counted in the office's
        Reports; never used for anything automatic (src/client_case.py)."""
        import client_case

        client_id = open_case(client_of(request))
        try:
            body = await request.json()
        except ValueError:
            body = None
        answer, error = client_case.clean_feedback(body)
        if error:
            raise HTTPException(400, f"feedback_{error}")
        asked = feedback_view(client_id, store.profile(client_id), store.profile(client_id).get("language", "pt"), store)
        if asked is None or asked["id"] != answer["step"]:  # only the step the page asked about
            raise HTTPException(400, "feedback_invalid")
        if limited(f"feedback:{client_id}", 20):
            raise HTTPException(429, "too_many_messages")
        store.add_feedback(client_id, answer["step"], asked["kind"], answer["face"], answer["comment"], store.profile(client_id).get("language", "pt"))
        cases = cases_folder()
        if (Path(cases) / client_id / "fact_graph.json").exists():  # the case is on this machine: the answer goes onto it at once
            client_case.sync_feedback(Path(cases) / client_id, store.root)
        return state(client_id)

    @app.get("/api/sheet/{n}")
    def preparation_sheet(n: int, request: Request):
        """One appointment's preparation sheet as a PDF, in the client's language: what the page already shows (src/journey.py client_view), drawn for the
        phone to keep or print."""
        import client_case

        client_id = open_case(client_of(request))
        lang = store.profile(client_id).get("language", "pt")
        view = (store.journey(client_id) or {}).get(lang) or (store.journey(client_id) or {}).get("en") or {}
        pages = view.get("prepare") or []
        if not 0 <= n < len(pages) or not pages[n].get("sheet"):
            raise HTTPException(404, "not found")
        p, labels = pages[n], view["prepare_labels"]
        when = client_case.day_words(p["date"], lang, True) + (f" · {client_case.clock_words(p['time'], lang)}" if p.get("time") else "")
        sure = bool(p.get("where_confirmed") and p.get("where"))  # an address nobody checked is not printed: the sheet says it is on the letter
        pdf = client_case.sheet_pdf(p["sheet"], {"when": when, "where": p["where"] if sure else None,
                                                 "note": labels["check_place"] if sure else labels["place_in_letter"]}, labels, firm_name())
        store.log(client_id, "sheet_downloaded", {})
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="preparation-sheet.pdf"'})

    @app.get("/api/questionnaire.pdf")
    def questionnaire_copy(request: Request):
        from .questionnaire_pdf import render
        st, client_id = who_of(request)
        open_case(client_id, st)
        with st._communication_gate(), st._lock:
            current_st, current_client = who_of(request)
            if current_st is not st or current_client != client_id:
                raise HTTPException(401, "sign in again")
            open_case(client_id, st)
            from .display_profile import current
            profile = current(st, client_id)
            if profile.get("status") != "submitted" or not profile.get("submitted_at"):
                raise HTTPException(409, "questionnaire_not_submitted")
            data = render(profile, st.answers(client_id))
            current_st, current_client = who_of(request)
            if current_st is not st or current_client != client_id:
                raise HTTPException(401, "sign in again")
            open_case(client_id, st)
            st.log(client_id, "questionnaire_copy_downloaded")
            return Response(data, media_type="application/pdf", headers={
                "Content-Disposition": 'attachment; filename="submitted-questionnaire.pdf"', "Cache-Control": "no-store"})

    @app.post("/api/submit")
    async def submit(request: Request):
        st, client_id = who_of(request)
        client_id, body = open_case(client_id, st), await request.json()
        current = state(client_id, st)
        if current["missing"]:
            raise HTTPException(400, "some required answers are missing")
        if not body.get("agree") or len(str(body.get("signature", "")).strip()) < 3:
            raise HTTPException(400, "please confirm and type your full name")
        st.update_profile(client_id, status="submitted", submitted_at=clock.stamp("seconds"),
                          attestation={"typed_name": str(body["signature"])[:120], "language": current["language"],
                                       "ip": address(request)})
        st.log(client_id, "submitted")
        (notifier if st is store else pnotifier).send(st.profile(client_id), "received")  # a prospect's goes through the prospects' folder: a restricted one is sent nothing
        if st is store:
            st.enqueue(client_id)  # a prospect is not processed into a case: the attorney reads the answers
        return state(client_id, st)

    @app.get("/api/signed-agreement.pdf")
    @app.get("/api/signing-certificate.pdf")
    def signed_agreement_copy(request: Request):
        import client_file
        import signing_evidence

        client_id = client_of(request)  # Identity comes only from the authenticated session.
        signed = store.engagement(client_id).get("signed") or {}
        letter_id = str(signed.get("letter") or "")
        if signed.get("how") != "portal" or not letter_id:
            raise HTTPException(404, "unavailable")
        d = Path(cases_folder()) / client_id
        try:
            client_file.allowed(d)
            if request.url.path == "/api/signing-certificate.pdf":
                return Response(signing_evidence.certificate(d, letter_id), media_type="application/pdf")
            result = signing_evidence.verify(d, letter_id)
            if not result["ok"]:
                raise HTTPException(409, "signing_record_unverifiable")
            raw = signing_evidence.paths(d, letter_id)[0].read_bytes()
            if signing_evidence.digest(raw) != result["letter_sha256"]:
                raise HTTPException(409, "signing_record_unverifiable")
            return Response(raw, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="signed-agreement.pdf"'})
        except (ValueError, OSError, LookupError):
            raise HTTPException(404, "unavailable") from None

    @app.post("/api/agreement")
    async def sign_agreement(request: Request):
        """The client signs the agreement: they ticked "I have read and agree" and typed their name, as on the questionnaire's last page. The
        typed name, the time, the address the request came from and the language they read it in are kept (portal/store.py); the firm's
        machine draws the signed copy on the case (src/engagement.py)."""
        client_id, body = open_case(client_of(request)), await request.json()
        if body.get("agree") is not True or len(str(body.get("signature", "")).strip()) < 3:
            raise HTTPException(400, "agree_first")
        if len(" ".join(str(body.get("signature") or "").split())) > 120:
            raise HTTPException(400, "name_too_long")  # said in the client's words by the page; never cut short without saying
        lang = store.profile(client_id).get("language", "pt")
        letter = store.engagement(client_id).get("letter") or {}
        consent = letter.get("consent") or {}
        if consent and (body.get("electronic_consent") is not True or consent.get("language") != (lang if lang in (letter.get("texts") or {}) else "en")):
            raise HTTPException(400, "agree_first")
        try:
            store.sign_agreement(client_id, str(body.get("letter") or ""), " ".join(str(body["signature"]).split()), address(request),
                                 lang if lang in (letter.get("texts") or {}) else "en")
        except LookupError:
            raise HTTPException(400, "unknown agreement") from None
        cases = cases_folder()
        if store.case_here(client_id, cases) or (Path(cases) / client_id / "engagement.json").exists():  # on this machine: on the case at once
            try:
                import engagement

                engagement.sync(Path(cases) / client_id, store.root)
            except Exception:  # noqa: BLE001 -- the review app records it when the case is next opened
                pass
        return state(client_id)

    def fee_waiver_to_case(client_id: str) -> None:
        """The client's opening or signing onto the case at once when it is on this machine (src/eoir26a.py sync); the review app records it when the case is next opened."""
        cases = cases_folder()
        if (Path(cases) / client_id / eoir26a.FILE).exists():
            eoir26a.sync(Path(cases) / client_id, store.root)

    @app.post("/api/fee-waiver-viewed")
    def fee_waiver_viewed(request: Request):
        """The client opened the fee waiver request to read it: the first time is kept, for the signing record."""
        client_id = open_case(client_of(request))
        if eoir26a.view_by_client(store, client_id):
            fee_waiver_to_case(client_id)
        return state(client_id)

    @app.post("/api/fee-waiver-sign")
    async def sign_fee_waiver(request: Request):
        """The client signs the fee waiver request as they sign the agreement: they ticked "I have read and agree" and typed their name, and drew it when the
        attorney allowed that. The typed name, the time, the address the request came from, the language and the figures they saw are kept (src/eoir26a.py)."""
        client_id, body = open_case(client_of(request)), await request.json()
        if limited(f"fee-waiver-sign:{client_id}", 30):
            raise HTTPException(429, "too_many_messages")
        lang = store.profile(client_id).get("language", "pt")
        try:
            eoir26a.sign_in_portal(store, client_id, body if isinstance(body, dict) else {}, address(request), lang)
        except LookupError:
            raise HTTPException(400, "fee_waiver_unknown") from None
        except ValueError as exc:
            if str(exc) == "name_not_client":
                fee_waiver_to_case(client_id)  # staff see the refusal on the Fee waiver tab
            raise HTTPException(400, str(exc)) from None
        fee_waiver_to_case(client_id)
        return state(client_id)

    return app


app = create_app()
