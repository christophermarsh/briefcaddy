"""Two-way messages: the client writes to the office from the portal ("Ask the
office": text only, no uploads), the office answers from the case in the
review app.

  - everything is kept per client in the portal store (messages.json, with who
    wrote it and when); nothing in a message leaves the installation. The only
    thing sent out is the notification "the office answered you, open your
    page" (portal/notify.py, kind office_reply): no case details, as with every
    message the system sends;
  - the office writes in English; the client reads the answer in their own
    language as a MACHINE DRAFT (Argos Translate, offline, through
    translate_for_client: the one place it is called), shown next to the
    office's own words. Haitian Creole has no Argos model (docs/decisions.md):
    the client reads the English with a line in Creole saying the office will
    follow up, and the review app says the answer needs a translator;
  - the staff member can change the draft before it is sent; a changed draft is
    the staff member's text, no longer a machine one.
"""

from __future__ import annotations

from typing import Any, Callable

from .bank import languages
from .notify import Notifier, delivery
from .store import PortalStore

OFFICE_LANGUAGE = "en"


def translate_for_client(text: str, lang: str) -> dict[str, Any]:
    """The office's words in the client's language: {"language", "text", "machine", "needs_translator"}. text is None when
    there is no model for the language (Haitian Creole, or a pair not installed): the answer then needs a human translator."""
    if lang == OFFICE_LANGUAGE:
        return {"language": lang, "text": text, "machine": False, "needs_translator": False}
    from .questions import machine_translate  # the one call into the offline translator (shared with typed questions)

    done = machine_translate(text, lang) if lang != "ht" else ""  # Argos has no Haitian Creole model
    return {"language": lang, "text": done or None, "machine": bool(done), "needs_translator": not done}


def reply(store: PortalStore, client_id: str, text: str, by: str, translation: str | None = None, reply_to: str | None = None,
          translate: Callable[[str, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """The office answers: stored in the thread with a draft in every language the client might read it in, and every waiting
    message from the client is settled (one answer covers what they wrote). translation: the staff member's own version in the
    client's language, replacing the machine draft there. translate: a stand-in for translate_for_client (tests, the demo)."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("Write the answer first.")
    lang = store.profile(client_id).get("language", "pt")
    drafts = {lg: (translate or translate_for_client)(text, lg) for lg in languages() if lg != OFFICE_LANGUAGE}
    if translation and translation.strip() and lang in drafts:
        drafts[lang] = {"language": lang, "text": translation.strip()[:4000], "machine": False, "needs_translator": False, "edited_by": by}
    message = store.add_message(client_id, "office", text, by=by, translations=drafts, **({"reply_to": reply_to} if reply_to else {}))
    store.handle_messages(client_id, by, reply_id=message["id"])
    return message


def notify(store: PortalStore, notifier: Notifier, client_id: str, link: str, message_id: str) -> dict[str, Any]:
    """"The office answered you, open your page": on the channels the client agreed to, with what really happened recorded on the message."""
    result = delivery(notifier.send(store.profile(client_id), "office_reply", link))
    store.update_message(client_id, message_id, delivery=result)
    return result


def thread_for_client(messages: list[dict[str, Any]], lang: str) -> list[dict[str, Any]]:
    """What the portal shows, in the client's language: their own messages as written; the office's answers as the draft in
    their language (with the office's own words under it) or, with no draft, the office's words and a note that the office
    will follow up ("followup": the portal writes that line in the client's language from its own screen texts)."""
    out = []
    for m in messages:
        if m["from"] == "client":
            out.append({"id": m["id"], "from": "client", "text": m["text"], "at": m["at"]})
            continue
        draft = (m.get("translations") or {}).get(lang) or {}
        shown = {"id": m["id"], "from": "office", "at": m["at"], "unseen": not m.get("seen_at")}
        if lang == OFFICE_LANGUAGE:
            out.append(shown | {"text": m["text"]})
        elif draft.get("text"):
            out.append(shown | {"text": draft["text"], "original": m["text"], "machine": bool(draft.get("machine"))})
        else:
            out.append(shown | {"text": m["text"], "followup": True})
    return out


def waiting(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The client's messages nobody at the office has answered or marked handled."""
    return [m for m in messages if m["from"] == "client" and m.get("status") == "new"]
