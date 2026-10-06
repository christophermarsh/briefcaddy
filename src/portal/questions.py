"""The office's own questions to a client ("Ask the client" in the review app),
typed and in the client's language (docs/design_plan.md Part 7, item 1).

The paralegal writes the question in English and says what kind of answer it
wants: words ("text"), a date ("date", a date picker in the portal), Yes or No
("yes_no") or one of a few choices ("choice"). The offline translator (Argos,
classify/translate.py) drafts it in the language the client reads the portal
in; the paralegal sees both and may correct the draft before anything is sent.
Both are stored on the request:

    {"type", "text" (= "text_en", the English the office wrote), "text_client",
     "language", "machine_translated", "needs_translator", "options"}

Argos has no Haitian Creole model, and a firm's machine may lack a language
pack: then "text_client" is empty, the request is marked "needs a translator",
and the question still goes out in English, with a line in the client's own
language that the office will follow up (portal.html "asked_in_english",
a DRAFT for the firm's certified translator like every client-facing text).

The client's answer comes back typed (a date as YYYY-MM-DD, "Yes"/"No", the
choice's English value) and staff read it in words (reply_words: a date as
MM/DD/YYYY).
"""

from __future__ import annotations

from typing import Any

from extract.base import ExtractedField

from .bank import _upper, all_questions, clean, language_names, load_bank

TYPES = ("text", "date", "yes_no", "choice")
OFFICE_DOC_ID = "office question"  # the source of a fact the client answered for the office: not a file, like the portal's own answers
OFFICE_DOC_TYPE = "office_question"
REPLY_MAX = 2000  # characters in a typed answer; longer is refused, not cut (portal.html shows the count)
MAX_OPTIONS = 8


def machine_translate(text: str, lang: str) -> str:
    """English -> the client's language with the offline translator; "" when it
    has no model for that pair (Haitian Creole has none) or returns the text
    unchanged. The one place the real call is made (tests replace it)."""
    from classify.translate import translate_text

    out = (translate_text(text, "en", lang) or "").strip() if text.strip() else ""
    return out if out and out != text.strip() else ""


def translate_to_english(text: str, lang: str) -> str:
    """A document's language -> English with the offline translator (src/translation.py); "" when it has no model for
    that pair (Haitian Creole has none) or the call fails. Unlike machine_translate, text that comes back unchanged is
    a translation (a name, a number, a place). The one place the real call is made for a document (tests replace it)."""
    from classify.translate import translate_text

    return (translate_text(text, lang, "en") or "").strip() if text.strip() else ""


def _options(raw: Any) -> list[str]:
    """The paralegal's choices: a list, or one per line; blanks and repeats dropped."""
    items = raw if isinstance(raw, list) else str(raw or "").splitlines()
    return list(dict.fromkeys(s for s in (str(x).strip()[:120] for x in items) if s))[:MAX_OPTIONS]


def draft(text_en: str, lang: str, options: Any = None) -> dict[str, Any]:
    """The question in the client's language as the translator drafts it, for the paralegal to see before
    it is added: {"language", "language_name", "text_client", "options_client", "needs_translator"}."""
    text_en, choices = str(text_en or "").strip(), _options(options)
    out = {"language": lang, "language_name": language_names().get(lang, lang)}
    if lang == "en":
        return out | {"text_client": text_en, "options_client": choices, "needs_translator": False}
    text = machine_translate(text_en, lang)
    labels = [machine_translate(o, lang) for o in choices]
    if lang == "pt":  # the firm's Portuguese-speaking clients are Brazilian; the translator writes European Portuguese (src/pt_br.py)
        import pt_br

        text, labels = pt_br.brazilian(text), [pt_br.brazilian(x) for x in labels]
    missing = not text or any(not x for x in labels)
    return out | {"text_client": text if not missing else "", "options_client": labels if not missing else ["" for _ in choices],
                  "needs_translator": missing}


def request_fields(body: dict[str, Any], lang: str) -> dict[str, Any]:
    """What add_request stores for a typed question, from the review app's body: "text" (English), "type",
    "options" (choices, English), and the client-language text the paralegal saw and may have corrected
    ("text_client", "options_client"; translated here when the body doesn't carry them)."""
    kind = str(body.get("type") or "text")
    if kind not in TYPES:
        raise ValueError("Choose what kind of answer you want: words, a date, Yes or No, or a choice.")
    text_en = str(body.get("text") or "").strip()
    choices = _options(body.get("options")) if kind == "choice" else []
    if kind == "choice" and len(choices) < 2:
        raise ValueError("Write at least two choices, one per line.")
    if body.get("doc_id") and kind != "text":
        kind = "text"  # a document request is answered by the upload, not a typed answer
    machine = draft(text_en, lang, choices)
    given = body.get("text_client")
    text_client = machine["text_client"] if given is None else str(given).strip()[:600]
    labels = machine["options_client"]
    if kind == "choice" and body.get("options_client") is not None:
        typed = _options(body.get("options_client"))
        labels = typed if len(typed) == len(choices) else labels
    if lang == "en":
        text_client, labels = text_en, choices
    complete = bool(text_client) and all(labels)  # a half-translated question goes in English, whole
    return {"type": kind, "text_client": text_client if complete else "", "language": lang,
            "machine_translated": lang != "en" and complete and text_client == machine["text_client"] and labels == machine["options_client"],
            "needs_translator": not complete,
            **({"options": [{"value": en, "en": en, "client": cl if complete else ""} for en, cl in zip(choices, labels)]} if kind == "choice" else {})}


def _yes_no(bank: dict[str, Any]) -> list[dict[str, Any]]:
    """Yes and No in every language, the question bank's own words (no new translation)."""
    for q in all_questions(bank).values():
        if q["type"] == "yes_no":
            return [o for o in q["options"] if o["value"] in ("Yes", "No")]
    return [{"value": "Yes", "label": {"en": "Yes"}}, {"value": "No", "label": {"en": "No"}}]


def for_client(request: dict[str, Any], lang: str, bank: dict[str, Any] | None = None) -> dict[str, Any]:
    """The request as the client's portal shows it, in the language they read now: the office's own translation
    when it was made for that language, else the English with "in_english" (the portal adds that the office will follow up)."""
    kind = request.get("type") or "text"
    fallback = (request.get("language_review") or {}).get("mode") == "english_fallback"
    own = bool(request.get("text_client")) and request.get("language") == lang and not fallback
    text = request["text_client"] if own else request.get("text_en") or request["text"]
    task = {"id": request["id"], "kind": "request", "type": kind, "text": text, "doc_id": request.get("doc_id"),
            "in_english": lang != "en" and not own,
            **({"fallback_reason": request["language_review"]["fallback_reason"], "intentional_english_fallback": True} if fallback else {})}
    if kind == "yes_no":
        # the buttons speak the question's language: English when the question went in English (never a mix)
        task["options"] = [{"value": o["value"], "label": (o["label"].get(lang) if own else None) or o["label"]["en"]} for o in _yes_no(bank or load_bank())]
    elif kind == "choice":
        task["options"] = [{"value": o["value"], "label": (o.get("client") if own else "") or o["en"]} for o in request.get("options") or []]
    return task


def clean_reply(request: dict[str, Any], value: Any) -> tuple[Any, str]:
    """The client's answer as stored: a date as YYYY-MM-DD, "Yes"/"No", a choice's value, or their words.
    -> (value, "" or the reason it isn't accepted)."""
    kind = request.get("type") or "text"
    if kind == "text":
        text = str(value or "").strip()
        if len(text) > REPLY_MAX:
            return None, "too_long"
        return (text, "") if text else (None, "empty")
    if kind == "date":
        cleaned, error = clean({"type": "date"}, value)
        return (cleaned, error) if cleaned or error else (None, "empty")
    options = [{"value": "Yes"}, {"value": "No"}] if kind == "yes_no" else request.get("options") or []
    cleaned, error = clean({"type": "choice", "options": options}, value)
    return (cleaned, error) if cleaned or error else (None, "empty")


def reply_words(request: dict[str, Any]) -> str:
    """The client's answer as staff read it: "Yes", a date as MM/DD/YYYY, the choice in English, their words."""
    reply = request.get("reply")
    if reply in (None, ""):
        return ""
    reply = str(reply)
    if (request.get("type") == "date") and len(reply) == 10 and reply[4] == "-":
        return f"{reply[5:7]}/{reply[8:10]}/{reply[:4]}"
    return reply


def asked_line(request: dict[str, Any]) -> str:
    """How the question reached the client, for staff: "Asked in Portuguese: ..." (the words the client read),
    or that it went in English because nobody translated it yet. "" for an English-reading client."""
    lang = request.get("language")
    if not lang or lang == "en":
        return ""
    name = language_names().get(lang, lang)
    if request.get("text_client"):
        return f"Asked in {name}: {request['text_client']}" + (" (machine translation)" if request.get("machine_translated") else "")
    return f"Asked in English (needs a translator for {name}): the client was told, in {name}, that the office will follow up."


def _answer_key(request: dict[str, Any], field_map: dict[str, Any]) -> str | None:
    """The one fact a typed answer fills, from the fact keys the question was asked about (the card's): the one the form
    reads that way (a date answers a date box, Yes or No a yes/no box, a choice the box that lists it). A question about a
    card with several boxes that fit, or none, fills nothing: the paralegal reads the answer and types it."""
    kind, reply, keys = request.get("type") or "text", request.get("reply"), request.get("facts") or []
    if reply in (None, "") or request.get("doc_id") or not keys:
        return None

    def fits(key: str) -> bool:
        spec = field_map.get(key)
        form = (spec.get("type", "text") if isinstance(spec, dict) else "text") if spec is not None else None
        if form is None:
            return len(keys) == 1  # not a box on the form: the one thing the question was about
        if kind == "date":
            return form == "date"
        if kind == "yes_no":
            return form == "yes_no" or (form == "choice_by_value" and {"Yes", "No"} <= set(spec.get("options") or []))
        if kind == "choice":
            return form == "choice_by_value" and reply in (spec.get("options") or [])
        return form == "text"

    fitting = [k for k in keys if fits(k)]
    return fitting[0] if len(fitting) == 1 else None


def office_answers(requests: list[dict[str, Any]], field_map: dict[str, Any]) -> list[ExtractedField]:
    """The client's typed answers to the office's questions as facts (Tier 3, the client's statement, like a portal answer:
    portal/engine.process_client records them beside the questionnaire's). A date as YYYY-MM-DD, Yes or No, the choice's
    value, words in capitals (the firm fills the form in capitals). The newest answer to a box wins. Each fact's raw value is
    the answer as staff read it; its source is OFFICE_DOC_ID (batch.process_documents)."""
    out: dict[str, ExtractedField] = {}
    for request in sorted((r for r in requests if r.get("status") == "answered"), key=lambda r: r.get("answered_at") or ""):
        key = _answer_key(request, field_map)
        if key:
            kind = request.get("type") or "text"
            out[key] = ExtractedField(key, reply_words(request), _upper(request["reply"]) if kind == "text" else str(request["reply"]), 0.95)
    return list(out.values())
