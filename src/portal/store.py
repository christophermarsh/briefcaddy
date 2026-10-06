"""Portal storage: one folder per client (profile, answers, uploads, tasks,
an append-only audit log), plus sign-in links and sessions.

Plain files on purpose -- the hybrid setup (docs/decisions.md) keeps the
portal's storage on firm-owned hosting and has the firm's own machine pull
from it; files sync and back up simply, and nothing here needs a database
server. Writes are atomic (temp file + rename) and serialized per process.

Security choices:
  - sign-in links and session cookies are random 32-byte tokens; only
    their SHA-256 is stored, so a copy of this folder can't be used to sign in;
  - links expire (72 h) and work once; sessions expire (12 h idle);
  - client ids are checked against a strict pattern before touching paths;
  - uploads are stored under random names, never the client's file name.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import clock
import events

from .bank import languages

CLIENT_ID = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}")
LINK_TTL = timedelta(hours=72)
SESSION_TTL = timedelta(hours=12)


MAX_UPLOAD = 15 * 1024 * 1024  # bytes: the portal's limit, and the staff's (review/operator.py)


def file_kind(data: bytes) -> str | None:
    """The content type a file really is, by its first bytes (never the browser's word for it): PDF, JPEG or PNG, else None."""
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def image_to_pdf(data: bytes) -> bytes:
    """A phone photo -> a one-page PDF, turned upright first (phones store
    the camera's orientation as an EXIF tag instead of rotating pixels)."""
    import io

    from PIL import Image, ImageOps

    if heic_photo(data):
        from pillow_heif import register_heif_opener
        register_heif_opener()
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception:  # noqa: BLE001 -- a file with a photo's first bytes and nothing after them (UnidentifiedImageError, a cut-off file)
        raise ValueError("That image can't be opened. Please add a clear photo or a PDF.") from None
    out = io.BytesIO()
    # A retry after receipt preparation must produce exactly the same PDF bytes.
    image.save(out, format="PDF", resolution=200, creationDate=time.gmtime(0), modDate=time.gmtime(0))
    return out.getvalue()


def heic_photo(data: bytes) -> bool:
    """Recognize HEIC container brands; decoding still validates the photo."""
    if len(data) < 16 or data[4:8] != b"ftyp":
        return False
    brands = [data[8:12]] + [data[n:n + 4] for n in range(16, min(int.from_bytes(data[:4], "big"), len(data), 64), 4)]
    return any(brand in {b"heic", b"heix", b"hevc", b"hevx"} for brand in brands)


def check_pdf(data: bytes) -> None:
    """Validate a readable, unencrypted page tree; no rendering or safety claim."""
    import io
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) < 1:
            raise ValueError("unreadable PDF")
    except Exception:  # malformed bytes/page tree, or password protection
        raise ValueError("That PDF can't be opened. Add an unencrypted PDF with at least one page.") from None


def _now() -> datetime:
    return clock.utcnow()  # the firm's clock in UTC (src/clock.py): a link's 72 hours are real hours, even the night the clocks change


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PortalStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "clients").mkdir(parents=True, exist_ok=True)
        (self.root / "queue").mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def communication_scope(self):
        """Exact main/prospect store composition; never infer it from outbox."""
        from .communication_consent import Scope
        root = self.root.absolute()
        if root.name == "portal" and root.parent.name == "data":
            return Scope(root.parent.parent, root, root.parent / "clients")
        if root.name == "prospects" and root.parent.name == "portal" and root.parent.parent.name == "data":
            return Scope(root.parent.parent.parent, root, root.parent.parent / "prospects")
        raise ValueError("This portal has no explicit installation communication scope.")

    def _communication_gate(self):
        from .communication_consent import gate
        try:
            scope = self.communication_scope()
        except ValueError:
            # Historical noninstallation stores may retain ordinary internal
            # profile data, but auth and Notifier fail closed for these stores.
            root = self.root.absolute()
            if root.parent.name == "data" or (root.name == "prospects" and root.parent.name == "portal" and root.parent.parent.name == "data"):
                raise  # never swallow a foreign installation override
            return nullcontext()
        return gate(scope)

    # -- files -----------------------------------------------------------------

    def _read(self, path: Path, default: Any) -> Any:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def _write(self, path: Path, data: Any) -> None:
        if path.name in {"auth.json", "profile.json"}:
            from .queue_bridge import _atomic
            _atomic(path, json.loads(json.dumps(data, default=str)))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, path)

    def client_dir(self, client_id: str) -> Path:
        if not CLIENT_ID.fullmatch(client_id or ""):
            raise LookupError("unknown client")
        return self.root / "clients" / client_id

    # -- clients -----------------------------------------------------------------

    def add_client(self, client_id: str, name: str, phone: str = "", email: str = "", language: str = "pt",
                   consent: dict[str, bool] | None = None, by: str = "firm", *, initial_profile: dict | None = None) -> dict[str, Any]:
        with self._communication_gate(), self._lock:
            from .contact_transitions import initial_fields
            setup = initial_fields(initial_profile or {})
            d = self.client_dir(client_id)
            old = self._read(d / "profile.json", {})
            profile = old | setup | {
                "id": client_id, "name": name, "phone": phone, "email": email,
                "language": language if language in languages() else "pt",
                "consent": consent or {"email": bool(email), "sms": False, "whatsapp": False},
            }
            profile.setdefault("status", "invited")
            profile.setdefault("created_at", _now().isoformat())
            pending = self._prepare_contact_change(client_id, old, profile)
            self._write(d / "profile.json", profile)
            self._finish_contact_change(client_id, pending)
            self.log(client_id, "client_added", {"by": by})  # "firm": the command line's import; a person's name: the review app's Add a client
            return profile

    def profile(self, client_id: str) -> dict[str, Any]:
        from .queue_bridge import _safe
        d = self.client_dir(client_id)
        _safe(d / "profile.json")
        if not (d / "profile.json").exists():
            raise LookupError("unknown client")
        return self._read(d / "profile.json", {})

    def update_profile(self, client_id: str, **changes: Any) -> dict[str, Any]:
        with self._communication_gate(), self._lock:
            old = self.profile(client_id)
            profile = old | changes
            pending = self._prepare_contact_change(client_id, old, profile)
            self._write(self.client_dir(client_id) / "profile.json", profile)
            self._finish_contact_change(client_id, pending)
            return profile

    def _prepare_contact_change(self, client, old, profile):
        from .communication_consent import prepare_profile_change
        try:
            scope = self.communication_scope()
        except ValueError:
            return False  # noninstallation stores cannot authenticate or send
        if not old:
            from .contact_transitions import prepare_enrollment
            identity, normalized = prepare_enrollment(scope, self, client, profile)
            profile.update(normalized)
            return {"contact": identity, "consent": False}
        from .promotion import guard_mutation
        guard_mutation(scope, self, client, profile)
        from .contact_transitions import normalize_profile, prepare_transition
        profile.update(normalize_profile(profile))
        contact = prepare_transition(scope, self, client, old, profile)
        changed = contact is not None or any(old.get(k) != profile.get(k) for k in ("language", "consent", "declined_on", "closed_on"))
        if not changed and contact is None:
            return None
        language_only = contact is None and {k for k in old.keys() | profile.keys() if old.get(k) != profile.get(k)} == {"language"}
        return {"contact": contact, "consent": prepare_profile_change(scope, self, client) if changed else False,
                "preserve_questionnaire": language_only}

    def _finish_contact_change(self, client, pending):
        if pending is None:
            return
        from .communication_consent import finish_profile_change
        try:
            scope = self.communication_scope()
        except ValueError:
            self.end_sessions(client)
            return
        if isinstance(pending, dict):
            finish_profile_change(scope, self, client, pending["consent"],
                                  preserve_questionnaire=pending.get("preserve_questionnaire", False))
            if pending["contact"]:
                from .contact_transitions import finish_transition
                finish_transition(scope, self, client, pending["contact"])
        else:
            finish_profile_change(scope, self, client, pending)

    def clients(self) -> list[str]:
        return sorted(p.name for p in (self.root / "clients").iterdir() if (p / "profile.json").exists())

    def find_by_contact(self, contact: str) -> str | None:
        """Exact unique identity across both installed stores; never a suffix."""
        from .contact_access import lookup_contact, utility_lookup
        try:
            scope = self.communication_scope()
        except ValueError:
            root = self.root.absolute()
            if root.parent.name == "data" or (root.name == "prospects" and root.parent.name == "portal"):
                return None  # foreign configured installation never becomes utility
            with self._lock:
                return utility_lookup(root, contact)
        result = lookup_contact(scope, contact)
        return result["client"] if result["matched"] and result["store_kind"] == scope.kind else None

    # -- answers, uploads, tasks ---------------------------------------------------------

    def answers(self, client_id: str) -> dict[str, Any]:
        return self._read(self.client_dir(client_id) / "answers.json", {})

    def save_answers(self, client_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            answers = self.answers(client_id)
            for k, v in changes.items():
                if v is None:
                    answers.pop(k, None)
                else:
                    answers[k] = v
            self._write(self.client_dir(client_id) / "answers.json", answers)
            self.log(client_id, "answers_saved", {"questions": sorted(changes)})
            return answers

    def uploads(self, client_id: str) -> list[dict[str, Any]]:
        return self._read(self.client_dir(client_id) / "uploads.json", [])

    def add_upload(self, client_id: str, doc_id: str, filename: str, data: bytes, content_type: str, retake: bool = False, *, capture=None, derivative=None, converted=None) -> dict[str, Any]:
        """retake: the photo answers a "send another photo" request (the office's list says "(retake)")."""
        if content_type not in ("application/pdf", "image/jpeg", "image/png"):
            raise ValueError("unsupported file type")
        if content_type == "application/pdf" and converted is not data:
            check_pdf(data)
        source_sha256 = hashlib.sha256(data).hexdigest()
        original = data
        if content_type != "application/pdf":
            if capture is None:
                from .capture_derivatives import original_attachment
                capture = original_attachment(original)
            data = converted if converted is not None else image_to_pdf(data)  # original, never the optional crop
        ext = ".pdf"
        with self._lock:
            d = self.client_dir(client_id)
            # "<requested document>-<random>.pdf": readable in the review app, never the client's own file name
            stored = f"{doc_id}-{secrets.token_hex(8)}{ext}"
            if capture:
                from . import capture_derivatives
                capture = capture_derivatives.bind(capture, stored[:-4])
                capture_derivatives.persist(self, client_id, capture, original, derivative)
            (d / "uploads").mkdir(parents=True, exist_ok=True)
            (d / "uploads" / stored).write_bytes(data)
            record = {"id": stored.rsplit(".", 1)[0], "doc_id": doc_id, "filename": filename[:120], "stored": stored, "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "source_sha256": source_sha256, "uploaded_at": _now().isoformat(), "status": "received",
                      **({"retake": True} if retake else {})}
            if capture:
                record["capture"] = capture
            self._write(d / "uploads.json", self.uploads(client_id) + [record])
            self.log(client_id, "upload", {"doc_id": doc_id, "size": len(data), "upload": record["id"]})
            return record

    def case_here(self, client_id: str, cases_root: str | Path) -> bool:
        """True when this machine holds the client's case and it was made from this client's own uploads (the portal and the review
        app share a machine): their new photos can be read at once. False for the portal on another host, or a client with no case yet."""
        case = Path(cases_root) / client_id
        try:
            meta = json.loads((case / "meta.json").read_text(encoding="utf-8")) if (case / "meta.json").exists() else {}
            return (case / "fact_graph.json").exists() and bool(meta.get("source_folder")) \
                and Path(meta["source_folder"]).resolve() == (self.client_dir(client_id) / "uploads").resolve()
        except (OSError, ValueError, LookupError):
            return False

    def mark_reading(self, client_id: str, reading: str | None) -> None:
        """What happens to the client's unread uploads: "now" (being read on this machine) or "tonight" (the next run reads them);
        None takes the mark off. The office's list says which."""
        with self._lock:
            uploads, changed = self.uploads(client_id), False
            for u in uploads:
                if u.get("status") == "received" and not u.get("source") and u.get("reading") != reading:
                    u.pop("reading", None)
                    if reading:
                        u["reading"] = reading
                    changed = True
            if changed:
                self._write(self.client_dir(client_id) / "uploads.json", uploads)

    def update_uploads(self, client_id: str, uploads: list[dict[str, Any]]) -> None:
        with self._lock:
            self._write(self.client_dir(client_id) / "uploads.json", uploads)

    def tasks(self, client_id: str) -> list[dict[str, Any]]:
        return self._read(self.client_dir(client_id) / "tasks.json", [])

    def save_tasks(self, client_id: str, tasks: list[dict[str, Any]]) -> None:
        with self._lock:
            self._write(self.client_dir(client_id) / "tasks.json", tasks)

    def mark_retakes_received(self, client_id: str, doc_id: str) -> int:
        """The client sent the new photo: the retake task for that document stays on their list as "received" (the portal says
        "Thank you, we have it") until the reader has read the photo, when the task goes (portal/engine.py)."""
        with self._lock:
            tasks, n = self.tasks(client_id), 0
            for t in tasks:
                if t.get("kind") == "retake" and t.get("doc_id") == doc_id and not t.get("received_at"):
                    t["received_at"] = _now().isoformat()
                    n += 1
            if n:
                self._write(self.client_dir(client_id) / "tasks.json", tasks)
            return n

    def journey(self, client_id: str) -> dict[str, Any] | None:
        """The case's stage in plain language, per language (src/journey.py writes it from the firm's machine)."""
        return self._read(self.client_dir(client_id) / "journey.json", None)

    def save_journey(self, client_id: str, views: dict[str, Any]) -> None:
        with self._lock:
            self._write(self.client_dir(client_id) / "journey.json", views)

    # -- the agreement and the end of the case (engagement.json; src/engagement.py writes the letter and the end from the firm's machine) ----

    def engagement(self, client_id: str) -> dict[str, Any]:
        """{letter: the agreement to read and sign, signed: the client's signature, ended: the case's end and its letter}, each optional."""
        data = self._read(self.client_dir(client_id) / "engagement.json", {})
        return data if isinstance(data, dict) else {}

    def save_engagement(self, client_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._write(self.client_dir(client_id) / "engagement.json", data)

    def sign_agreement(self, client_id: str, letter_id: str, typed_name: str, address: str, language: str) -> dict[str, Any]:
        """The client signs the agreement on their page: the typed name, the time, the address the request came from and the language they read
        it in. The firm's machine puts it on the case (src/engagement.py sync). Signing twice keeps the first."""
        with self._lock:
            data = self.engagement(client_id)
            letter = data.get("letter")
            if not letter or letter.get("id") != letter_id or data.get("ended"):
                raise LookupError("unknown agreement")
            if not data.get("signed"):
                data["signed"] = {"letter": letter_id, "how": "portal", "typed_name": typed_name, "at": clock.stamp("seconds"), "address": address,
                                  "language": language, "letter_snapshot": letter,
                                  "consent": {"kind": "electronic" if (letter.get("consent") or {}).get("text") else "read_and_agree",
                                              "accepted": True, "text": (letter.get("consent") or {}).get("text") or __import__("signing_evidence").READ_AND_AGREE.get(language, "I have read and agree"),
                                              "language": language, "approval": letter.get("consent")}}
                self._write(self.client_dir(client_id) / "engagement.json", data)
                self.log(client_id, "agreement_signed", {"letter": letter_id})
            return data

    # -- the fee waiver request (fee_waiver.json and fee_waiver/; src/eoir26a.py writes the request from the firm's machine) ----------------------------

    def fee_waiver(self, client_id: str) -> dict[str, Any]:
        """{request: the office's request (id, at), signing: the signing opened (sentence, drawn), viewed: when the client opened it, signed: their
        signature}, each optional."""
        data = self._read(self.client_dir(client_id) / "fee_waiver.json", {})
        return data if isinstance(data, dict) else {}

    def save_fee_waiver(self, client_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._write(self.client_dir(client_id) / "fee_waiver.json", data)

    def note_fee_waiver_viewed(self, client_id: str, request_id: str) -> bool:
        """The client opened the request to read it: the first time is kept (the audit page says when it was viewed). True when this was the first."""
        with self._lock:
            data = self.fee_waiver(client_id)
            if (data.get("request") or {}).get("id") != request_id or data.get("viewed"):
                return False
            data["viewed"] = clock.stamp("seconds")
            self._write(self.client_dir(client_id) / "fee_waiver.json", data)
            self.log(client_id, "fee_waiver_viewed", {"request": request_id})
            return True

    def refuse_fee_waiver_name(self, client_id: str, request_id: str) -> None:
        """The client typed a name that is not the one the case holds: nothing is signed, and the date is kept for staff ("The client tried to sign as
        another name on ..."). The name typed is not kept: it may be someone else's."""
        with self._lock:
            data = self.fee_waiver(client_id)
            if (data.get("request") or {}).get("id") != request_id or data.get("signed"):
                return
            data["name_refused"] = (data.get("name_refused") or []) + [{"request": request_id, "at": clock.stamp("seconds")}]
            self._write(self.client_dir(client_id) / "fee_waiver.json", data)
            self.log(client_id, "fee_waiver_name_refused", {"request": request_id})

    def sign_fee_waiver(self, client_id: str, request_id: str, signed: dict[str, Any], drawing: bytes | None = None) -> dict[str, Any]:
        """The client signs the fee waiver request on their page: signed holds the typed name, the address the request came from, the language they
        read it in and the figures they saw (src/eoir26a.py builds it). A drawn signature is kept as an image beside the record. Signing twice keeps
        the first."""
        with self._lock:
            data = self.fee_waiver(client_id)
            request, signing = data.get("request"), data.get("signing")
            if not request or request.get("id") != request_id or not signing or data.get("ended") or self.engagement(client_id).get("ended"):
                raise LookupError("unknown request")
            if not data.get("signed"):
                record = dict(signed) | {"request": request_id, "how": "portal", "at": clock.stamp("seconds")}
                if drawing:
                    name = f"signature-{request_id}-{secrets.token_hex(4)}.png"
                    folder = self.client_dir(client_id) / "fee_waiver"
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / name).write_bytes(drawing)
                    try:
                        os.chmod(folder / name, 0o600)  # the owner only, as the roster and the vault are
                    except OSError:
                        pass
                    record["drawn"] = {"file": name, "sha256": hashlib.sha256(drawing).hexdigest()}
                data["signed"] = record
                self._write(self.client_dir(client_id) / "fee_waiver.json", data)
                self.log(client_id, "fee_waiver_signed", {"request": request_id})
            return data

    # -- "how was this step": one tap after a milestone (feedback.json; src/client_case.py copies it onto the case) ----------------------------------

    def feedback(self, client_id: str) -> list[dict[str, Any]]:
        data = self._read(self.client_dir(client_id) / "feedback.json", [])
        return data if isinstance(data, list) else []

    def add_feedback(self, client_id: str, step: str, kind: str, face: str, comment: str, language: str) -> dict[str, Any] | None:
        """The client's answer about one step: a face and an optional sentence. The first answer for a step stays (None for a second). The audit log and the
        ledger say that an answer was given, never what it says."""
        with self._lock:
            given = self.feedback(client_id)
            if any(r.get("step") == step for r in given):
                return None
            row = {"step": step, "kind": kind, "face": face, "comment": comment, "at": clock.stamp("seconds"), "language": language}
            self._write(self.client_dir(client_id) / "feedback.json", given + [row])
            self.log(client_id, "feedback_given", {"kind": kind})
            return row

    def stopped(self, client_id: str) -> bool:
        """A person the firm declined: their link and sessions stop working from the day of the letter (src/engagement.py)."""
        try:  # closed_on: a prospect that became a client (src/prospects.py): its link and sessions stop the day it did
            profile = self.profile(client_id)
            day = str(profile.get("declined_on") or profile.get("closed_on") or "")
        except LookupError:
            return False
        return bool(day) and day <= clock.today().isoformat()

    def end_sessions(self, client_id: str, *, preserve_questionnaire: bool = False) -> None:
        """End credentials; a language-only update may retain staff handovers."""
        with self._communication_gate(), self._lock:
            auth = self._auth()
            for table in ("links", "sessions"):
                auth[table] = {k: v for k, v in auth.get(table, {}).items()
                               if v.get("client") != client_id or preserve_questionnaire and v.get("purpose") == "staff_questionnaire"}
            self._write(self.root / "auth.json", auth)

    # -- messages: the client's notes to the office and the office's answers (messages.json) --------
    # Text only, kept here and nowhere else: nothing in a message leaves the installation except the "open your page" notification.

    def messages(self, client_id: str) -> list[dict[str, Any]]:
        return self._read(self.client_dir(client_id) / "messages.json", [])

    def add_message(self, client_id: str, sender: str, text: str, by: str | None = None, **extra: Any) -> dict[str, Any]:
        """sender: "client" (status "new" until the office answers or marks it handled) or "office" (by = who answered).
        The audit log records that a message came and who wrote it, never what it says."""
        text = re.sub(r"\n{3,}", "\n\n", str(text or "")).strip()[:2000]
        if not text:
            raise ValueError("empty message")
        with self._lock:
            message = {"id": f"msg-{secrets.token_hex(4)}", "from": sender, "text": text, "at": _now().isoformat(),
                       **({"by": by} if by else {}), **({"status": "new"} if sender == "client" else {}), **extra}
            self._write(self.client_dir(client_id) / "messages.json", self.messages(client_id) + [message])
            self.log(client_id, f"message_from_{sender}", {"message": message["id"], **({"by": by} if by else {})})
            return message

    def update_message(self, client_id: str, message_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            messages = self.messages(client_id)
            message = next((m for m in messages if m["id"] == message_id), None)
            if message is None:
                raise LookupError("unknown message")
            message.update(changes)
            self._write(self.client_dir(client_id) / "messages.json", messages)
            return message

    def handle_messages(self, client_id: str, by: str, message_id: str | None = None, reply_id: str | None = None) -> list[str]:
        """The office has dealt with a client's message(s): one by id, or every waiting one (an answer in the thread settles them all)."""
        with self._lock:
            messages, done = self.messages(client_id), []
            for m in messages:
                if m["from"] == "client" and m.get("status") == "new" and message_id in (None, m["id"]):
                    m.update(status="handled", handled_by=by, handled_at=_now().isoformat(), **({"reply": reply_id} if reply_id else {}))
                    done.append(m["id"])
            if done:
                self._write(self.client_dir(client_id) / "messages.json", messages)
                self.log(client_id, "messages_handled", {"messages": done, "by": by})
            return done

    def mark_messages_seen(self, client_id: str) -> int:
        """The client opened their thread: every answer from the office is now seen (the badge goes)."""
        with self._lock:
            messages = self.messages(client_id)
            fresh = [m for m in messages if m["from"] == "office" and not m.get("seen_at")]
            for m in fresh:
                m["seen_at"] = _now().isoformat()
            if fresh:
                self._write(self.client_dir(client_id) / "messages.json", messages)
                self.log(client_id, "messages_seen", {})
            return len(fresh)

    # -- requests from the office (kept apart from tasks.json, which the worker rebuilds) ----

    def requests(self, client_id: str) -> list[dict[str, Any]]:
        from .request_readiness import scope_for, check
        scope = scope_for(self)
        if scope is not None:
            with self._communication_gate(), self._lock:
                rows = self._read(self.client_dir(client_id) / "requests.json", [])
                return [dict(row, language_hold=None if (state := check(scope, self, client_id, row))["ready"] else state["reason"]) for row in rows]
        return self._read(self.client_dir(client_id) / "requests.json", [])

    def client_requests(self, client_id: str) -> list[dict[str, Any]]:
        """Current client-visible wording; staff requests() retains every draft/hold."""
        return [row for row in self.requests(client_id) if not row.get("language_hold")]

    def add_request(self, client_id: str, text: str, doc_id: str | None, by: str, draft: bool = False,
                    facts: list[str] | None = None, typed: dict[str, Any] | None = None) -> dict[str, Any]:
        """A question or document the paralegal asks the client for, from the review app. A draft waits in the
        office's list (the client doesn't see it) until send_drafts() sends everything waiting as one message.
        typed: the kind of answer and the client-language text (portal/questions.request_fields); "text" stays the
        English the office wrote (also "text_en")."""
        with self._communication_gate(), self._lock:
            request = {"id": f"req-{secrets.token_hex(4)}", "text": text.strip()[:600], "doc_id": doc_id or None, "by": by,
                       "asked_at": _now().isoformat(), "status": "draft" if draft else "open", **({"facts": list(facts)} if facts else {}),
                       **({"text_en": text.strip()[:600]} | typed if typed else {})}
            # Caller-provided typed metadata cannot import an approval.
            for key in ("language_review", "language_reviews", "language_hold"):
                request.pop(key, None)
            from .request_readiness import scope_for, check
            scope = scope_for(self)
            if scope is not None:
                state = check(scope, self, client_id, request)
                request["language_hold"] = None if state["ready"] else state["reason"]
                if not state["ready"]:
                    request["status"] = "draft"
            self._write(self.client_dir(client_id) / "requests.json", self.requests(client_id) + [request])
            self.log(client_id, "request", {"request": request["id"], "by": by, "doc_id": doc_id})
            return request

    def send_drafts(self, client_id: str, by: str) -> list[dict[str, Any]]:
        """Every request waiting in the office's list goes to the client now (status open)."""
        with self._communication_gate(), self._lock:
            requests = self.requests(client_id)
            sent = [r for r in requests if r["status"] == "draft" and not r.get("language_hold")]
            for r in sent:
                r.update(status="open", asked_at=_now().isoformat(), sent_by=by)
            self._write(self.client_dir(client_id) / "requests.json", requests)
            if sent:
                self.log(client_id, "requests_sent", {"requests": [r["id"] for r in sent], "by": by})
            return sent

    def skip_drafts(self, client_id: str, why_for: dict[str, str]) -> list[dict[str, Any]]:
        """Retires every waiting question whose facts the office has already decided (why_for: fact key -> why), so the
        client is never asked what the office settled: the request stays on file with status "skipped" and the reason."""
        with self._communication_gate(), self._lock:
            requests, skipped = self.requests(client_id), []
            for r in requests:
                if r["status"] == "draft" and r.get("facts") and all(k in why_for for k in r["facts"]):
                    r.update(status="skipped", skipped_at=_now().isoformat(), skipped_why=why_for[r["facts"][0]])
                    skipped.append(r)
            if skipped:
                self._write(self.client_dir(client_id) / "requests.json", requests)
                self.log(client_id, "requests_skipped", {"requests": [r["id"] for r in skipped]})
            return skipped

    def skipped_tasks(self, client_id: str) -> list[dict[str, Any]]:
        """The client's to-do items left out because the office had already decided them (portal/engine.py)."""
        return self._read(self.client_dir(client_id) / "skipped_tasks.json", [])

    def add_skipped_tasks(self, client_id: str, records: list[dict[str, Any]]) -> None:
        with self._lock:
            known = {r["task"] for r in self.skipped_tasks(client_id)}
            fresh = [r for r in records if r["task"] not in known]
            if fresh:
                self._write(self.client_dir(client_id) / "skipped_tasks.json", self.skipped_tasks(client_id) + fresh)
                self.log(client_id, "tasks_skipped", {"tasks": [r["task"] for r in fresh]})

    def mark_delivery(self, client_id: str, request_ids: list[str], delivery: dict[str, Any]) -> None:
        """Records on each request what the message about it really did (portal/notify.delivery): queued, sent or failed."""
        with self._communication_gate(), self._lock:
            requests = self.requests(client_id)
            for r in requests:
                if r["id"] in request_ids:
                    r["delivery"] = delivery
            self._write(self.client_dir(client_id) / "requests.json", requests)
            self.log(client_id, "request_delivery", {"requests": request_ids, "status": delivery["status"], "text": delivery["text"]})

    def settle_requests(self, client_id: str, by: str, request_ids: list[str] | None = None) -> list[str]:
        """The office has read the client's answers: each answered request is settled (settled_at, settled_by), and the portal's
        "Sent to the office" line for it goes. request_ids None: every answered one."""
        with self._communication_gate(), self._lock:
            requests, done = self.requests(client_id), []
            for r in requests:
                if r["status"] == "answered" and not r.get("settled_at") and (request_ids is None or r["id"] in request_ids):
                    r.update(settled_at=_now().isoformat(), settled_by=by)
                    done.append(r["id"])
            if done:
                self._write(self.client_dir(client_id) / "requests.json", requests)
                self.log(client_id, "requests_settled", {"requests": done, "by": by})
            return done

    def close_requests(self, client_id: str, request_ids: list[str], by: str) -> list[str]:
        """The office's open requests for a paper it has marked absent (src/absence.py) are closed: the client is not asked again (portal/engine.py
        sync_absences). Kept on file with who and when, so the mark's Undo opens them again (reopen_closed_requests)."""
        with self._communication_gate(), self._lock:
            requests, done = self.requests(client_id), []
            for r in requests:
                if r["status"] == "open" and r["id"] in request_ids:
                    r.update(status="closed", closed_at=_now().isoformat(), closed_by=by, closed_by_absence=True)
                    done.append(r["id"])
            if done:
                self._write(self.client_dir(client_id) / "requests.json", requests)
                self.log(client_id, "requests_closed", {"requests": done, "by": by})
            return done

    def reopen_closed_requests(self, client_id: str, request_ids: list[str]) -> list[str]:
        """The requests closed for a paper whose mark is off (undone, or lifted when the paper arrived) are open again."""
        with self._communication_gate(), self._lock:
            requests, done = self.requests(client_id), []
            for r in requests:
                if r["status"] == "closed" and r.get("closed_by_absence") and r["id"] in request_ids:
                    for k in ("closed_at", "closed_by", "closed_by_absence"):
                        r.pop(k, None)
                    r["status"] = "open"
                    done.append(r["id"])
            if done:
                self._write(self.client_dir(client_id) / "requests.json", requests)
                self.log(client_id, "requests_reopened", {"requests": done})
            return done

    def drop_draft(self, client_id: str, request_id: str) -> None:
        with self._communication_gate(), self._lock:
            requests = self.requests(client_id)
            if not any(r["id"] == request_id and r["status"] == "draft" for r in requests):
                raise LookupError("Only a question that hasn't been sent can be removed.")
            self._write(self.client_dir(client_id) / "requests.json", [r for r in requests if r["id"] != request_id])

    def answer_request(self, client_id: str, request_id: str, reply: str | None = None, upload: str | None = None) -> dict[str, Any]:
        """The client's answer (already checked against the question's type, portal/questions.clean_reply) or upload."""
        with self._communication_gate(), self._lock:
            requests = self.requests(client_id)
            request = next((r for r in requests if r["id"] == request_id and r["status"] in ("open", "answered")), None)
            if request is None:
                raise LookupError("unknown request")
            if request.get("language_hold"):
                raise LookupError("Request language review is incomplete or changed.")
            request.update(status="answered", answered_at=_now().isoformat(),
                           **({"reply": str(reply).strip()[:2000]} if reply else {}), **({"upload": upload} if upload else {}))
            self._write(self.client_dir(client_id) / "requests.json", requests)
            self.log(client_id, "request_answered", {"request": request_id})
            return request

    # -- processing queue (the firm machine's worker pulls from it) -----------------------

    def enqueue(self, client_id: str) -> dict:
        from .queue_bridge import enqueue
        return enqueue(self, client_id)

    def queue_generation(self, client_id: str) -> dict | None:
        from .queue_bridge import generation
        return generation(self, client_id)

    def acknowledge_queue(self, client_id: str, generation: str) -> str:
        from .queue_bridge import acknowledge
        return acknowledge(self, client_id, generation)

    def queued(self) -> list[str]:
        return sorted(p.name for p in (self.root / "queue").iterdir() if CLIENT_ID.fullmatch(p.name))

    def dequeue(self, client_id: str) -> None:
        raise ValueError("Queue removal requires the exact completed generation; use acknowledge_queue.")

    # -- audit log ----------------------------------------------------------------------

    def log(self, client_id: str, event: str, detail: dict[str, Any] | None = None) -> None:
        line = json.dumps({"at": _now().isoformat(), "event": event, **(detail or {})}, ensure_ascii=False)
        with self._lock, open(self.client_dir(client_id) / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
        self._ledger(client_id, event, detail or {})

    # The portal's own log (above) is one client's; the firm's event ledger (src/events.py) is every case's. A change to a portal record is a row
    # there too: what happened and who did it, never an answer, a message or a file. client: the client did it themselves (else the staff member
    # acting, or the one named in the row, or the command line that imported them).
    _LEDGER = {"client_added": ("added", "Added the client to the portal", False), "answers_saved": ("answered", "Saved answers to {n} question(s)", True),
               "upload": ("uploaded", "Uploaded a document", True), "request": ("asked", "Asked the client for an answer or a document", False),
               "requests_sent": ("asked", "Sent {n} request(s) to the client", False), "request_answered": ("answered", "Answered a request from the office", True),
               "message_from_client": ("wrote", "Sent the office a message", True), "message_from_office": ("wrote", "The office answered the client's message", False),
               "messages_handled": ("handled", "Marked the client's message(s) as dealt with", False), "requests_settled": ("settled", "Settled {n} answered request(s)", False),
               "requests_skipped": ("skipped", "Left out {n} question(s) the office had already decided", False),
               "requests_closed": ("closed", "Closed {n} request(s) for a paper the office recorded the client has none of", False),
               "requests_reopened": ("reopened", "Opened {n} request(s) again: the paper is no longer recorded as absent", False),
               "tasks_skipped": ("skipped", "Left out {n} item(s) on the client's list that the office had already decided", False),
               "questionnaire_changed": ("changed", "Changed the client's questionnaire", False), "invited": ("invited", "Sent the client an invitation to the portal", False),
               "reminded": ("reminded", "Sent the client a reminder", False), "language_changed": ("changed", "The client chose another language for the portal", True),
               "messages_seen": ("read", "The client read the office's answer(s)", True),
               "questionnaire_copy_downloaded": ("downloaded", "The client downloaded their submitted questionnaire", True),
               "agreement_sent": ("sent", "Put the agreement on the client's page to sign", False),
               "agreement_signed": ("signed", "The client signed the agreement on their page", True),
               "feedback_given": ("answered", "The client said how a step went", True),
               "fee_waiver_sent": ("sent", "Put the fee waiver questions on the client's page", False),
               "fee_waiver_opened": ("sent", "Opened the fee waiver request on the client's page to sign", False),
               "fee_waiver_viewed": ("read", "The client opened the fee waiver request", True),
               "fee_waiver_signed": ("signed", "The client signed the fee waiver request on their page", True),
               "fee_waiver_name_refused": ("refused", "The client tried to sign the fee waiver request as another name: refused", True),
               "appointment_reminded": ("reminded", "Sent the client a reminder about an appointment", False),
               "case_ended": ("ended", "The client's page now says the case with the office has ended", False),
               "case_reopened": ("reopened", "The client's page works again: the case was opened again", False),
               "imported_from_docketwise": ("imported", "Imported the client from Docketwise", False)}

    def _ledger_where(self, client_id: str, kind: str) -> tuple[str, str, Path]:
        """(the ledger's kind, the case it names, where the ledger is) for a row about this client. The prospects' store (src/prospects.py) says otherwise."""
        return kind, client_id, self.root.parent

    def _ledger(self, client_id: str, event: str, detail: dict[str, Any]) -> None:
        action, text, by_client = self._LEDGER.get(event, (None, None, False))
        if action is None:
            return
        n = len(next((detail[k] for k in ("questions", "requests", "tasks") if isinstance(detail.get(k), list)), []))
        by = str(detail.get("by") or "")
        imported = event == "imported_from_docketwise" or by == "firm"  # "firm": the command line's import (portal/admin.py, tools/import_docketwise.py)
        kind, case, home = self._ledger_where(client_id, "imports" if event == "imported_from_docketwise" else "portal")
        events.record(kind, action, text.format(n=n), case=case, home=home,
                      who=None if imported or not by else by, version=1,
                      default_who=("The client", "client", "portal") if by_client and not events.actor()
                      else ("The importer", "system", "importer") if imported else ("The product", "system", "system"))

    # -- sign-in links and sessions --------------------------------------------------------

    def _auth(self) -> dict[str, Any]:
        from .communication_consent import _read
        auth = _read(self.root / "auth.json", {"links": {}, "sessions": {}})
        if not all(isinstance(auth.get(k), dict) and all(isinstance(v, dict) for v in auth[k].values()) for k in ("links", "sessions")):
            raise ValueError("Portal credentials are damaged.")
        return auth

    def new_link_token(self, client_id: str) -> str:
        raise PermissionError("Sign-in credentials are issued during accepted dispatch; use the protected consent-only manual link action for assisted signoff.")

    def redeem_link(self, token: str) -> str | None:
        """A link works once: it becomes a session token (returned), or None."""
        from .communication_consent import credential_valid, gate, manual_valid
        from .contact_control import _publish
        from . import staff_access
        try:
            scope = self.communication_scope()
            with gate(scope), self._lock:
                auth = self._auth()
                link = auth["links"].pop(_hash(token or ""), None)
                handed = isinstance(link, dict) and link.get("purpose") == "staff_questionnaire"
                valid = staff_access.valid(scope, self, link) if handed else manual_valid(scope, self, link) if isinstance(link, dict) and link.get("purpose") == "consent_only" else credential_valid(scope, self, link)
                # A crash after consumption cannot replay a link to publish
                # proof or a session. Keep this write separate and first.
                self._write(self.root / "auth.json", auth)
                if not valid:
                    return None
                if link.get("purpose") != "consent_only" and not handed:
                    receipt = _publish(scope, self, link, _hash(token))
                    link = dict(link, control_receipt=receipt)
                session = secrets.token_urlsafe(32)
                auth["sessions"][_hash(session)] = dict(link, expires=(_now() + SESSION_TTL).isoformat())
                self._write(self.root / "auth.json", auth)
        except TimeoutError:
            raise  # A busy installation is not an invalid client session.
        except (ValueError, OSError, LookupError, TypeError, KeyError):
            return None
        self.log(link["client"], "signed_in")
        return session

    def session_client(self, session: str | None, *, consent_only=False, closed_artifacts=False) -> str | None:
        if not session:
            return None
        from .communication_consent import gate, manual_valid
        from .contact_control import session_valid
        from . import staff_access
        try:
            scope = self.communication_scope()
            with gate(scope), self._lock:
                auth = self._auth()
                entry = auth["sessions"].get(_hash(session))
                manual = isinstance(entry, dict) and entry.get("purpose") == "consent_only"
                handed = isinstance(entry, dict) and entry.get("purpose") == "staff_questionnaire"
                limited = manual or isinstance(entry, dict) and entry.get("purpose") == "verify_contact"
                valid = staff_access.valid(scope, self, entry) if handed else manual_valid(scope, self, entry) if manual else session_valid(scope, self, entry, allow_verification=True)
                closed = not limited and not valid and session_valid(scope, self, entry, closed_artifacts=True)
                if closed:
                    if not closed_artifacts:
                        return None  # preserve read-only end access, never upgrade general access
                    entry["expires"] = (_now() + SESSION_TTL).isoformat()
                    self._write(self.root / "auth.json", auth)
                    return entry["client"]
                if not valid:
                    auth["sessions"].pop(_hash(session), None)
                    self._write(self.root / "auth.json", auth)
                    return None
                if closed_artifacts:
                    return None  # consent-only or still-open principals are not closed-artifact access
                if limited and not consent_only:
                    return None
                entry["expires"] = (_now() + SESSION_TTL).isoformat()
                self._write(self.root / "auth.json", auth)
                return entry["client"]
        except TimeoutError:
            raise  # Keep the session valid while document processing holds the gate.
        except (ValueError, OSError, LookupError, TypeError, KeyError):
            return None

    def end_session(self, session: str | None) -> None:
        with self._communication_gate(), self._lock:
            auth = self._auth()
            auth["sessions"].pop(_hash(session or ""), None)
            self._write(self.root / "auth.json", auth)
