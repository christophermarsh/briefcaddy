"""Messages to clients: email, SMS and WhatsApp.

Rules every message follows:
  - no personal data in the message -- only the firm's name and a secure
    sign-in link (texts and emails are not a safe channel for case details) -- except the appointment
    reminders (src/client_reminders.py), which carry the day, the time and the place of the appointment
    because that is what they are for; a restricted case gets none of them (below);
  - only channels the client agreed to (profile["consent"]); SMS and
    WhatsApp carry "reply STOP" wording;
  - in the client's language;
  - none at all, on any channel, for a restricted case (src/restricted.py: VAWA, T, U, asylum, or a case an
    attorney restricted) unless an attorney switched automatic messages on for that case with a reason: a
    text from the firm on a shared or watched phone can itself put the client at risk. The office reaches
    the client by hand; what was asked still waits in the client's portal.

Providers are configured with environment variables; a channel with no
provider writes to data/portal/outbox.jsonl instead of sending (dry run),
so nothing goes out by accident during a pilot. A sign-in link is a
working credential for 72 hours, so the outbox keeps only its last 6
characters ("/l/…Xy12ab"); PORTAL_OUTBOX_FULL_LINKS=1 writes the whole
link, for tests and local demos only (never on a server with real clients).

E-mail goes over STARTTLS with the system's certificate checks
(ssl.create_default_context): Python's smtplib does not check the mail
server's certificate unless it is given a context (3.12's starttls()
falls back to ssl._create_stdlib_context, which checks nothing).

  email:     SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, MAIL_FROM   (Amazon SES, SendGrid... all speak SMTP)
  SMS:       TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_SMS_FROM
  WhatsApp:  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM    (WhatsApp Business via Twilio; messages
             outside a 24-hour reply window must use templates Meta has approved -- set TWILIO_WHATSAPP_TEMPLATE_<KIND>)
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import clock
import firmsecrets  # keys and secrets are read by name (src/firmsecrets.py)

FIRM = "Case Review"  # the name used when Settings has none: no firm's name is built into the product


def firm_name() -> str:
    """The firm's name as the Settings page has it (Firm name), else FIRM: on every message and on the portal's top bar."""
    try:
        import settings

        name = settings.firm_name()
    except Exception:  # noqa: BLE001 -- a message must never fail for want of a setting
        name = ""
    return name or FIRM


TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "verify_contact": {  # Draft: exact language bundle requires actual reviewed wording.
        "en": {"subject": "{firm}: verify your phone", "body": "{firm}: confirm this phone for office messages using this secure link: {link}. This link only verifies your phone; request a separate sign-in link to access your portal."},
        "pt": {"subject": "{firm}: confirme seu telefone", "body": "{firm}: confirme este telefone para mensagens do escritório neste link seguro: {link}. Este link só confirma seu telefone; peça outro link de acesso para entrar no portal."},
        "es": {"subject": "{firm}: confirme su teléfono", "body": "{firm}: confirme este teléfono para mensajes de la oficina en este enlace seguro: {link}. Este enlace solo confirma su teléfono; solicite otro enlace de acceso para entrar al portal."},
        "ht": {"subject": "{firm}: verifye telefòn ou", "body": "{firm}: konfime telefòn sa a pou mesaj biwo a ak lyen ki an sekirite sa a: {link}. Lyen sa a sèlman verifye telefòn ou; mande yon lòt lyen pou antre nan pòtal la."},
    },
    "invite": {
        "pt": {"subject": "{firm}: seu questionário do green card", "body": "Olá! O escritório {firm} preparou o seu questionário para o pedido de green card. Comece aqui (link seguro): {link}"},
        "es": {"subject": "{firm}: su cuestionario de green card", "body": "¡Hola! La oficina {firm} preparó su cuestionario para la solicitud de green card. Empiece aquí (enlace seguro): {link}"},
        "en": {"subject": "{firm}: your green card questionnaire", "body": "Hello! {firm} has prepared your green card questionnaire. Start here (secure link): {link}"},
        "ht": {"subject": "{firm}: kesyonè green card ou", "body": "Bonjou! Biwo {firm} prepare kesyonè pou demann green card ou. Kòmanse isit la (lyen ki an sekirite): {link}"},
    },
    "invite_n400": {
        "pt": {"subject": "{firm}: seu questionário de cidadania", "body": "Olá! O escritório {firm} preparou o seu questionário para o pedido de cidadania americana. Comece aqui (link seguro): {link}"},
        "es": {"subject": "{firm}: su cuestionario de ciudadanía", "body": "¡Hola! La oficina {firm} preparó su cuestionario para la solicitud de ciudadanía estadounidense. Empiece aquí (enlace seguro): {link}"},
        "en": {"subject": "{firm}: your citizenship questionnaire", "body": "Hello! {firm} has prepared your U.S. citizenship questionnaire. Start here (secure link): {link}"},
        "ht": {"subject": "{firm}: kesyonè sitwayènte ou", "body": "Bonjou! Biwo {firm} prepare kesyonè pou demann sitwayènte ameriken ou. Kòmanse isit la (lyen ki an sekirite): {link}"},
    },
    "invite_parole": {  # DRAFT: attorney and certified translator
        "pt": {"subject": "{firm}: seu questionário do pedido de parole", "body": "Olá! O escritório {firm} preparou o seu questionário para o pedido de parole humanitário (permissão para uma pessoa entrar nos EUA). Comece aqui (link seguro): {link}"},
        "es": {"subject": "{firm}: su cuestionario de la solicitud de parole", "body": "¡Hola! La oficina {firm} preparó su cuestionario para la solicitud de parole humanitario (permiso para que una persona entre a los EE. UU.). Empiece aquí (enlace seguro): {link}"},
        "en": {"subject": "{firm}: your humanitarian parole questionnaire", "body": "Hello! {firm} has prepared your questionnaire for the humanitarian parole request (permission for a person to enter the U.S.). Start here (secure link): {link}"},
        "ht": {"subject": "{firm}: kesyonè parole imanitè ou", "body": "Bonjou! Biwo {firm} prepare kesyonè pou demann parole imanitè a (pèmisyon pou yon moun antre Etazini). Kòmanse isit la (lyen ki an sekirite): {link}"},
    },
    "invite_first_contact": {  # DRAFT: attorney and certified translator; a person who is not yet a client (src/prospects.py)
        "pt": {"subject": "{firm}: algumas perguntas antes de conversarmos", "body": "Olá! O escritório {firm} preparou algumas perguntas para você responder antes de conversarmos. Comece aqui (link seguro): {link}"},
        "es": {"subject": "{firm}: unas preguntas antes de hablar", "body": "¡Hola! La oficina {firm} preparó unas preguntas para que las responda antes de que hablemos. Empiece aquí (enlace seguro): {link}"},
        "en": {"subject": "{firm}: a few questions before we talk", "body": "Hello! {firm} has prepared a few questions for you to answer before we talk. Start here (secure link): {link}"},
        "ht": {"subject": "{firm}: kèk kesyon anvan nou pale", "body": "Bonjou! Biwo {firm} prepare kèk kesyon pou ou reponn anvan nou pale. Kòmanse isit la (lyen ki an sekirite): {link}"},
    },
    "reminder": {
        "pt": {"subject": "{firm}: falta pouco no seu questionário", "body": "Lembrete do escritório {firm}: seu questionário ainda não foi enviado. Continue aqui: {link}"},
        "es": {"subject": "{firm}: falta poco en su cuestionario", "body": "Recordatorio de {firm}: su cuestionario aún no fue enviado. Continúe aquí: {link}"},
        "en": {"subject": "{firm}: your questionnaire is almost done", "body": "Reminder from {firm}: your questionnaire hasn't been submitted yet. Continue here: {link}"},
        "ht": {"subject": "{firm}: kesyonè ou prèske fini", "body": "Rapèl biwo {firm}: ou poko voye kesyonè ou. Kontinye isit la: {link}"},
    },
    "request": {
        "pt": {"subject": "{firm}: precisamos de algo de você", "body": "O escritório {firm} pediu uma informação ou documento para o seu caso. Veja aqui: {link}"},
        "es": {"subject": "{firm}: necesitamos algo de usted", "body": "La oficina {firm} pidió información o un documento para su caso. Véalo aquí: {link}"},
        "en": {"subject": "{firm}: we need something from you", "body": "{firm} has asked for information or a document for your case. See it here: {link}"},
        "ht": {"subject": "{firm}: nou bezwen yon bagay nan men ou", "body": "Biwo {firm} mande yon enfòmasyon oswa yon dokiman pou dosye ou. Gade l isit la: {link}"},
    },
    "case_update": {
        "pt": {"subject": "{firm}: novidade no seu caso", "body": "Há uma novidade no seu caso com o escritório {firm}. Veja aqui (link seguro): {link}"},
        "es": {"subject": "{firm}: novedad en su caso", "body": "Hay una novedad en su caso con la oficina {firm}. Véala aquí (enlace seguro): {link}"},
        "en": {"subject": "{firm}: news about your case", "body": "There is news about your case with {firm}. See it here (secure link): {link}"},
        "ht": {"subject": "{firm}: gen nouvèl sou dosye ou", "body": "Gen nouvèl sou dosye ou ak biwo {firm}. Gade l isit la (lyen ki an sekirite): {link}"},
    },
    # the office answered a message the client wrote in the portal: no word of the answer or the case, only "open your page" (DRAFT: attorney and certified translator)
    "office_reply": {
        "pt": {"subject": "{firm}: o escritório respondeu", "body": "O escritório {firm} respondeu à sua mensagem. Abra a sua página (link seguro): {link}"},
        "es": {"subject": "{firm}: la oficina le respondió", "body": "La oficina {firm} respondió a su mensaje. Abra su página (enlace seguro): {link}"},
        "en": {"subject": "{firm}: the office answered you", "body": "{firm} answered your message. Open your page (secure link): {link}"},
        "ht": {"subject": "{firm}: biwo a reponn ou", "body": "Biwo {firm} reponn mesaj ou an. Louvri paj ou a (lyen ki an sekirite): {link}"},
    },
    "received": {
        "pt": {"subject": "{firm}: questionário recebido", "body": "Recebemos o seu questionário. O escritório {firm} vai revisar e entrar em contato."},
        "es": {"subject": "{firm}: cuestionario recibido", "body": "Recibimos su cuestionario. La oficina {firm} lo revisará y se comunicará con usted."},
        "en": {"subject": "{firm}: questionnaire received", "body": "We received your questionnaire. {firm} will review it and contact you."},
        "ht": {"subject": "{firm}: nou resevwa kesyonè a", "body": "Nou resevwa kesyonè ou. Biwo {firm} ap revize l epi l ap kontakte ou."},
    },
}
# The appointment reminders (src/client_reminders.py): the week before and the day before a fingerprint appointment, an interview or a court hearing. The one
# message that carries the day, the time and the place, because the client asked to be reminded of exactly those; every other message above carries none of
# them. DRAFT for the attorney and a certified translator; the Haitian Creole is a machine draft. A restricted case gets no reminder (Notifier.allowed).
TEMPLATES["appointment_week"] = {
    "pt": {"subject": "{firm}: o seu compromisso em {date}", "body": "Lembrete do escritório {firm}: você tem {what} em {date}{time}.{place} Leve a sua carta e o seu passaporte.{phone}"},
    "es": {"subject": "{firm}: su cita el {date}", "body": "Recordatorio de {firm}: usted tiene {what} el {date}{time}.{place} Lleve su carta y su pasaporte.{phone}"},
    "en": {"subject": "{firm}: your appointment on {date}", "body": "Reminder from {firm}: you have {what} on {date}{time}.{place} Bring your notice and your passport.{phone}"},
    "ht": {"subject": "{firm}: randevou ou a nan dat {date}", "body": "Rapèl biwo {firm}: ou gen {what} nan dat {date}{time}.{place} Pote lèt ou a ak paspò ou.{phone}"},
}
TEMPLATES["appointment_day"] = {
    "pt": {"subject": "{firm}: o seu compromisso é amanhã", "body": "Lembrete do escritório {firm}: você tem {what} amanhã, {date}{time}.{place} Leve a sua carta e o seu passaporte.{phone}"},
    "es": {"subject": "{firm}: su cita es mañana", "body": "Recordatorio de {firm}: usted tiene {what} mañana, {date}{time}.{place} Lleve su carta y su pasaporte.{phone}"},
    "en": {"subject": "{firm}: your appointment is tomorrow", "body": "Reminder from {firm}: you have {what} tomorrow, {date}{time}.{place} Bring your notice and your passport.{phone}"},
    "ht": {"subject": "{firm}: randevou ou a se demen", "body": "Rapèl biwo {firm}: ou gen {what} demen, {date}{time}.{place} Pote lèt ou a ak paspò ou.{phone}"},
}
APPOINTMENT_WORDS: dict[str, dict[str, dict[str, str]]] = {
    "what": {
        "biometrics": {"pt": "a sua coleta de digitais", "es": "su toma de huellas", "en": "your fingerprint appointment", "ht": "randevou anprent dwèt ou a"},
        "interview": {"pt": "a sua entrevista", "es": "su entrevista", "en": "your interview", "ht": "entèvyou ou a"},
        "hearing": {"pt": "a sua audiência no tribunal", "es": "su audiencia en la corte", "en": "your court hearing", "ht": "odyans ou nan tribinal la"},
    },
    "time": {"pt": " às {time}", "es": " a las {time}", "en": " at {time}", "ht": " a {time}"},
    # the place goes only when a person entered or checked it (a hearing's court, an address confirmed or typed on the case); each form of it is followed by
    # the same sentence the client's page uses (schemas/registers/journey.json appointment_pages): check it against the letter
    "place": {"pt": " Local: {where}.", "es": " Lugar: {where}.", "en": " Place: {where}.", "ht": " Kote: {where}."},
    "check": {"pt": " Confira este endereço na sua carta.", "es": " Revise esta dirección en su carta.", "en": " Check this address on your letter.",
              "ht": " Verifye adrès sa a sou lèt ou a."},
    "check_court": {"pt": " Confira o tribunal e a sala na notificação da audiência.", "es": " Revise la corte y la sala en la notificación de la audiencia.",
                    "en": " Check the court and the room on your hearing notice.", "ht": " Verifye tribinal la ak sal la sou avi odyans lan."},
    "noplace": {"pt": " O endereço está na sua carta. Confira lá.", "es": " La dirección está en su carta. Revísela allí.", "en": " The address is on your letter. Check it there.",
                "ht": " Adrès la ekri sou lèt ou a. Verifye l la."},
    "phone": {"pt": " Dúvidas? Ligue para o escritório: {phone}.", "es": " ¿Preguntas? Llame a la oficina: {phone}.", "en": " Questions? Call the office: {phone}.",
              "ht": " Kesyon? Rele biwo a: {phone}."},
}
STOP = {"pt": " Responda PARE para não receber mais.", "es": " Responda STOP para no recibir más.", "en": " Reply STOP to opt out.",
        "ht": " Reponn STOP pou ou pa resevwa mesaj ankò."}


def render(kind: str, language: str, link: str = "", **fields: str) -> dict[str, str]:
    template = TEMPLATES[kind].get(language) or TEMPLATES[kind]["en"]
    firm = firm_name()
    return {"subject": template["subject"].format(firm=firm, link=link, **fields), "body": template["body"].format(firm=firm, link=link, **fields)}


def delivery(results: list[dict[str, str]], now: datetime | None = None) -> dict[str, str]:
    """What a send really did, for the screen: {"status", "text", "at"}. A channel with no provider only wrote to the
    outbox (dry run), so it is "queued", never "sent"; "sent" means the provider accepted it. status is "sent" (every
    channel), "queued" (something waited in the outbox, nothing failed), "failed", or "none" (no channel the client agreed to)."""
    now = now or clock.now()
    names = {"email": "email", "sms": "text", "whatsapp": "WhatsApp"}
    absent = {"email": "no mail server configured", "sms": "no text service configured", "whatsapp": "no WhatsApp service configured"}
    parts, states = [], set()
    for r in results:
        result, channel = str(r.get("result") or ""), r.get("channel", "")
        if result == "skipped":
            continue
        if result.startswith("dry-run"):
            states.add("queued")
            parts.append(f"Queued, {absent.get(channel, 'no provider configured')}")
        elif result == "sent":
            states.add("sent")
            parts.append(f"Sent by {names.get(channel, channel)}")
        else:
            states.add("failed")
            parts.append(f"Failed by {names.get(channel, channel)}; check the {'mail' if channel == 'email' else 'text message'} settings")
    if any(r.get("why") == NO_CASES for r in results):
        return {"status": "failed", "text": "Not sent: the case folders were not found, so nobody can tell whether this is a restricted case. Ask your IT.",
                "at": now.isoformat()}
    if any(r.get("why") == RESTRICTED for r in results):  # nothing went out: the office reaches the client by hand
        import restricted

        return {"status": "hand", "text": restricted.HAND, "at": now.isoformat()}
    held = next((r for r in results if r.get("why") == CONFLICT), None)
    if held is not None:  # the conflict check waits for an attorney, or the client was declined (src/conflicts.py): nothing goes
        import conflicts

        return {"status": "held", "text": "Not sent. " + (held.get("text") or conflicts.HELD), "at": now.isoformat()}
    status = "failed" if "failed" in states else "queued" if "queued" in states else "sent" if states else "none"
    text = "; ".join(p if i == 0 else p[0].lower() + p[1:] for i, p in enumerate(parts)) or "Not sent: no channel the client agreed to"
    return {"status": status, "text": text, "at": now.isoformat()}


RESTRICTED = "restricted case"  # the "why" of a message refused for a restricted case
NO_CASES = "case folders not found"  # the "why" when the case folders are not where the installation says: nothing is sent
CONFLICT = "conflict check waiting"  # the "why" of a message held while the conflict check waits for an attorney or after a decline (src/conflicts.py held)


def cases_folder() -> Path:
    """The installation's case folders: I485_CASES (set it when they are not in data/clients beside the code), else data/clients."""
    return Path(os.environ.get("I485_CASES") or Path(__file__).resolve().parents[2] / "data" / "clients")


def prospects_folder() -> Path:
    """The installation's prospects (src/prospects.py): I485_PROSPECTS, else the folder beside the case folders (data/prospects beside data/clients). A prospect
    of a protected kind is restricted there (access.json) exactly as a case is in cases_folder(), and the Notifier reads it from there."""
    return Path(os.environ.get("I485_PROSPECTS") or cases_folder().parent / "prospects")


class Notifier:
    def __init__(self, outbox: str | Path, env: dict[str, str] | None = None, cases_root: str | Path | None = None, *, store=None):
        """cases_root: the case folders, where a client's restricted-case record lives (src/restricted.py): as given (the review
        app passes its own), else I485_CASES, else the installation's data/clients. Never guessed from where the portal's
        folder is: PORTAL_DATA may be anywhere."""
        self.outbox = Path(outbox)
        self.env = dict(os.environ if env is None else env)
        self.cases_root = Path(cases_root) if cases_root else cases_folder()
        self.store = store
        self.scope = None
        try:
            if self.store is None:
                # Trusted installation config/case composition, never outbox.
                from .store import PortalStore
                from .communication_consent import Scope
                portal = Path(os.environ.get("PORTAL_DATA") or self.cases_root.parent / "portal")
                if self.cases_root.name == "prospects":
                    import prospects
                    Scope(self.cases_root.absolute().parent.parent, portal.absolute() / "prospects", self.cases_root.absolute())
                    self.store = prospects.store(portal)
                else:
                    Scope(self.cases_root.absolute().parent.parent, portal.absolute(), self.cases_root.absolute())
                    self.store = PortalStore(portal)
            self.scope = self.store.communication_scope()
            if self.scope.cases != self.cases_root.absolute():
                self.scope = None
        except (ValueError, OSError):
            self.scope = None

    def allowed(self, profile: dict[str, Any]) -> bool:
        """THE guard for every message and every sign-in link to a client (an invitation, a reminder, a request, the office's reply, "there's
        news", the portal's own "send me a link", the command line's "invite everyone"): every path that makes a link asks this first, and
        makes none when it says no. No for a restricted case (src/restricted.py) unless an attorney switched messages on, and no for a client
        whose conflict check waits for an attorney or who was declined (src/conflicts.py held). Fails closed: when the case folders are not
        where this installation says, nothing can be checked, so nothing is sent."""
        from .communication_consent import eligibility
        return bool(self.scope and eligibility(self.scope, self.store, str(profile.get("id") or ""), "email")["allowed"])

    def _open(self, profile: dict[str, Any]) -> bool:
        """Not a restricted case, or one whose automatic messages an attorney switched on."""
        import restricted

        client_id = str(profile.get("id") or "")
        if not client_id:
            return True  # a profile with no case behind it yet
        if "/" in client_id or "\\" in client_id or client_id.startswith(".") or not self.cases_root.is_dir():
            return False
        case = self.cases_root / client_id
        if restricted.kind_law(profile.get("track") or profile.get("docketwise_matter_type")) and not (case / restricted.FILE).exists() \
                and not (case / "fact_graph.json").exists():
            return False  # a client of a protected kind with no record and no case file yet (added before records were made at once, or the record is gone): nothing, as for its case
        return restricted.messages_allowed(case)

    def held(self, profile: dict[str, Any], kind: str | None = None) -> bool:
        """A client whose conflict check waits for an attorney's decision, or who was declined (src/conflicts.py), or whose case with the office
        ended: declined, withdrawn, transferred or closed (src/engagement.py): no message of any kind, and no sign-in link. kind is not looked at:
        every kind is held."""
        import conflicts

        client_id = str(profile.get("id") or "")
        if profile.get("declined_on"):
            return True
        if not client_id or "/" in client_id or "\\" in client_id or client_id.startswith("."):
            return False
        return conflicts.held(self.cases_root / client_id) or self._ended(client_id)

    def _ended(self, client_id: str) -> bool:
        import engagement

        return engagement.end_info(self.cases_root / client_id) is not None

    def hold_words(self, profile: dict[str, Any]) -> str:
        """Why nothing goes to this client, in words (the conflict check's, else the end of the case's)."""
        import conflicts
        import engagement

        client_id = str(profile.get("id") or "")
        words = conflicts.hold_words(self.cases_root / client_id) if client_id else ""
        return words or (engagement.ENDED_HELD if self._ended(client_id) or profile.get("declined_on") else "")

    def send(self, profile: dict[str, Any], kind: str, link: str = "", skip_channels: Any = (), **fields: str) -> list[dict[str, str]]:
        """Sends on every channel the client consented to; returns what was
        done per channel (sent / dry-run / skipped + why). A restricted case:
        nothing on any channel, and one row that says why. fields: the words a template fills in besides the firm and the link (the appointment reminders)."""
        from .communication_consent import dispatch, gate
        if not self.scope or not self.cases_root.is_dir():
            return [{"channel": "all", "result": "skipped", "why": NO_CASES}]
        client = str(profile.get("id") or "")
        with gate(self.scope):
            try:
                current = self.store.profile(client)
            except (ValueError, LookupError, OSError):
                return [{"channel": "all", "result": "skipped", "why": "client unavailable"}]
            if not self._open(current):
                return [{"channel": "all", "result": "skipped", "why": RESTRICTED}]
            if self.held(current):
                return [{"channel": "all", "result": "skipped", "why": CONFLICT, "text": self.hold_words(current)}]
            if kind in ("request", "reminder"):
                from .request_readiness import notification_hold
                held = notification_hold(self.scope, self.store, client)
                if held:
                    return [{"channel": "all", "result": "skipped", "why": "request_language_review_required", "text": held}]
            lang = current.get("language", "pt")
            variant = f"{kind}_{current.get('filing')}"
            message_kind = variant if variant in TEMPLATES else kind
            results = []
            for channel, address in (("email", current.get("email")), ("sms", current.get("phone")), ("whatsapp", current.get("phone"))):
                if not address or channel in skip_channels:
                    continue
                provider_result = []
                def provider(destination, token):
                    # Caller-supplied pre-created credentials are never sent.
                    base = self.env.get("PORTAL_BASE_URL", "http://localhost:8600").rstrip("/")
                    channel_link = f"{base}/l/{token}" if token else ""
                    message = render(message_kind, lang, channel_link, **fields)
                    body = message["body"] + (STOP.get(lang, STOP["en"]) if channel in ("sms", "whatsapp") else "")
                    result = getattr(self, f"_{channel}")(destination, message["subject"], body, message_kind)
                    provider_result.append(result)
                    return {"status": "sent" if result == "sent" else "dry-run" if str(result).startswith("dry-run") else "failed"}
                try:
                    outcome = dispatch(self.scope, self.store, client, channel, provider, credential=kind not in {"received"} and not kind.startswith("appointment_"))
                    result = {"channel": channel, "result": provider_result[-1] if provider_result else "skipped" if outcome["status"] == "held" else "failed: unknown",
                              **({"why": outcome["reason"]} if outcome.get("reason") else {})}
                except (ValueError, OSError, LookupError, TimeoutError):
                    result = {"channel": channel, "result": "failed: permission or dispatch recording unavailable"}
                results.append(result)
            return results

    def verify_contact(self, profile: dict[str, Any], channel: str, *, actor_email: str) -> dict[str, Any]:
        """Explicit authorized neutral phone challenge, never a substantive template."""
        from .communication_consent import gate, verification_dispatch
        if not self.scope or channel not in {"sms", "whatsapp"}:
            return {"status": "held", "reason": "unsupported_verification_channel"}
        client = str(profile.get("id") or "")
        with gate(self.scope):
            current = self.store.profile(client)
            def provider(destination, token):
                base = self.env.get("PORTAL_BASE_URL", "http://localhost:8600").rstrip("/")
                message = render("verify_contact", current["language"], f"{base}/l/{token}")
                body = message["body"] + STOP[current["language"]]
                result = getattr(self, f"_{channel}")(destination, message["subject"], body, "verify_contact")
                return {"status": "sent" if result == "sent" else "dry-run" if str(result).startswith("dry-run") else "failed"}
            return verification_dispatch(self.scope, self.store, client, channel, provider, actor_email=actor_email)

    def staff_email(self, to: str, subject: str, body: str) -> str:
        """An e-mail to a staff member who turned reminders on (src/staff_reminders.py): the same mail server or outbox as the clients'. Not a message
        to a client, so no client consent and no restricted-case check applies; the reminder names a case only as the calendar feed's rule allows."""
        return self._email(to, subject, body, "staff_reminder")

    # -- channels ---------------------------------------------------------------

    def _dry(self, channel: str, to: str, subject: str, body: str) -> str:
        if self.env.get("PORTAL_OUTBOX_FULL_LINKS") != "1":  # a sign-in link is a credential: only its last 6 characters
            body = re.sub(r"(/l/)([A-Za-z0-9_-]+)", lambda m: f"{m[1]}…{m[2][-6:]}", body)
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        with open(self.outbox, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": clock.stamp(), "channel": channel, "to": to,
                                "subject": subject, "body": body}, ensure_ascii=False) + "\n")
        return "dry-run (outbox)"

    def _email(self, to: str, subject: str, body: str, kind: str) -> str:
        host = self.env.get("SMTP_HOST")
        if not host:
            return self._dry("email", to, subject, body)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.env["MAIL_FROM"], to, subject
        msg.set_content(body)
        with smtplib.SMTP(host, int(self.env.get("SMTP_PORT", "587")), timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())  # checks the server's certificate and name
            if self.env.get("SMTP_USER"):
                smtp.login(self.env["SMTP_USER"], firmsecrets.get("smtp.password", env=self.env, data_root=self.scope.data if self.scope else self.cases_root.parent) or "")
            smtp.send_message(msg)
        return "sent"

    def _twilio(self, data: dict[str, str]) -> str:
        from .opt_out import outbound_ready
        channel = "whatsapp" if data.get("From", "").startswith("whatsapp:") else "sms"
        if not self.scope or not outbound_ready(self.scope, channel, env=self.env):
            return "held: authenticated STOP configuration required"
        import httpx

        sid = self.env["TWILIO_ACCOUNT_SID"]
        response = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=data,
                              auth=(sid, firmsecrets.get("twilio.auth_token", env=self.env, data_root=self.scope.data if self.scope else self.cases_root.parent) or ""), timeout=20)
        response.raise_for_status()
        return "sent"

    def _sms(self, to: str, subject: str, body: str, kind: str) -> str:
        if not (self.env.get("TWILIO_ACCOUNT_SID") and self.env.get("TWILIO_SMS_FROM")):
            return self._dry("sms", to, subject, body)
        return self._twilio({"From": self.env["TWILIO_SMS_FROM"], "To": to, "Body": body})

    def _whatsapp(self, to: str, subject: str, body: str, kind: str) -> str:
        if not (self.env.get("TWILIO_ACCOUNT_SID") and self.env.get("TWILIO_WHATSAPP_FROM")):
            return self._dry("whatsapp", to, subject, body)
        data = {"From": f"whatsapp:{self.env['TWILIO_WHATSAPP_FROM']}", "To": f"whatsapp:{to}"}
        template = self.env.get(f"TWILIO_WHATSAPP_TEMPLATE_{kind.upper()}")
        if template:  # business-initiated messages need an approved template (Meta's rule)
            data |= {"ContentSid": template, "ContentVariables": json.dumps({"1": body})}
        else:
            data["Body"] = body
        return self._twilio(data)
