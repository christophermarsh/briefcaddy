"""Foreign-language documents in a filing: the English translation, the translator's certificate, and the summary
a reviewer reads without the language (docs/design_plan.md Part 5).

What USCIS asks. 8 CFR 103.2(b)(3), "Translations" (eCFR, read 10/02/2026, section 103.2 as it stands on that date):

    "Any document containing foreign language submitted to USCIS shall be accompanied by a full English language
    translation which the translator has certified as complete and accurate, and by the translator's certification
    that he or she is competent to translate from the foreign language into English."

So the exhibit is the original, a full English translation, and the translator's certification of both things:
complete and accurate, and competent. The certificate below says exactly those two things (CERTIFICATE_WORDING),
with the translator's name and statement of competence from the Settings page (settings.translators).

What is built, for each foreign-language document the packet holds (a type in TYPES whose language is one the
offline translator reads: documents.language()):

  - the English text, made once with the offline translator (Argos, classify/translate.py; nothing leaves the
    machine) and kept in the document record's "translated" (documents.set_translated); the translator may correct it
    or, for a language the machine has no model for (Haitian Creole), type it;
  - translations.json in the case folder: who made it and how, who typed or corrected it, which translator the
    attorney chose, and when the translator signed -- each with who and when, kept through every reprocessing;
  - translations/translation-<id>.pdf: the side-by-side translation (the original page image on the left, the English
    text on the right, a header naming the document, the person and the language) followed by the certificate of
    translation, drawn with the review bundle's PDF tooling (review/bundle.py). Both are marked DRAFT, with
    "DRAFT machine translation, not for filing until the translator signs" at the foot, until the named translator
    is recorded as having signed; the signed copy loses the DRAFT marks and carries "signed MM/DD/YYYY".

The packet (packet.plan) puts the signed copy in the exhibit next to the original and accepts it as the document's
translation; a document with only a draft, or none, is a line on the "before you mail it" list. Only a signed copy is
ever printed in a packet. The translator signs the printed certificate in ink: the packet's list says so.

Haitian Creole and any language whose model isn't installed: the record says so in words ("needs a human translator",
"the model isn't installed") and the page shows it; nothing is translated and nothing fails silently.

The summary (summaries) is built from what the extractors found in the document (the fact graph's sources for it),
never from a model's prose: what it is, whose, the dates and names found.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import unicodedata
from datetime import date

import clock
import events
from pathlib import Path
from typing import Any

from pypdf import PdfWriter

from fill.continuation import HEIGHT, MARGIN, WIDTH

log = logging.getLogger(__name__)

FOLDER = "translations"
STATE = "translations.json"
SOURCE = "8 CFR 103.2(b)(3), https://www.ecfr.gov/current/title-8/section-103.2 (read 10/02/2026)"
REGULATION = ("Any document containing foreign language submitted to USCIS shall be accompanied by a full English language translation "
              "which the translator has certified as complete and accurate, and by the translator's certification that he or she is "
              "competent to translate from the foreign language into English.")
# The certificate's one sentence: both certifications the regulation names, nothing more.
CERTIFICATE_WORDING = ("I, {name}, certify that I am competent to translate from {language} into English, and that the attached "
                       "English translation of the {document} of {person} is complete and accurate.")
DRAFT_MACHINE = "DRAFT machine translation, not for filing until the translator signs"
DRAFT_TYPED = "DRAFT translation, not for filing until the translator signs"
DRAFT_CERTIFICATE = "DRAFT certificate of translation, not for filing until the named translator signs"

# The documents a filing needs translated when they are not in English (the packet's exhibits for identity, relationship and
# records). The birth certificate and the marriage certificate first; the others are the records a case commonly carries.
TYPES = ("birth_certificate", "marriage_certificate", "divorce_decree", "police_clearance", "criminal_record", "court_disposition")
FOREIGN = ("pt", "es", "fr", "ht")  # the languages the offline translator is asked about; documents.language() names pt, es, ht
CREOLE_PROBLEM = ("Needs a human translator: the offline translator has no Haitian Creole model. The translator types the English "
                  "translation below, then the certificate can be made.")
EMPTY_PROBLEM = "No text could be read from this document, so there was nothing to translate. The translator types the translation from the original."
PEOPLE = {"applicant": "the client", "spouse": "the client's spouse", "petitioner": "the petitioner", "parent": "a parent of the client",
          "unknown": "a person not yet set"}
TEXT_MAX = 60000  # characters in a translation typed or corrected on the screen


def _now() -> str:
    return clock.stamp()


def _words(code: str) -> str:
    import settings

    return settings.TRANSLATOR_LANGUAGES.get(code) or code.upper()


def person_words(person: str) -> str:
    if person in PEOPLE:
        return PEOPLE[person]
    m = re.fullmatch(r"child_(\d+)", person or "")
    return f"the client's child (number {m.group(1)})" if m else PEOPLE["unknown"]


def needs(record: dict[str, Any]) -> bool:
    """A document in a foreign language, of a kind the packet must have translated."""
    return record.get("type") in TYPES and foreign_language(record) is not None


def foreign_language(record: dict[str, Any]) -> str | None:
    """The foreign language the document is translated from: the one on its record, or, when a person changed it to English and chose to keep the
    translation (documents.set_language, "translation_kept"), the language it was before. None when no translation is needed."""
    lang = record.get("language")
    if lang in FOREIGN:
        return lang
    kept = record.get("translation_kept") or {}
    return kept.get("language") if kept.get("keep") and kept.get("language") in FOREIGN else None


def translators() -> list[dict[str, Any]]:
    import settings

    return settings.translators()


def engine() -> str:
    try:
        from importlib.metadata import version

        return f"Argos Translate {version('argostranslate')}, offline on this machine"
    except Exception:  # noqa: BLE001 -- the version is a note, never a reason to fail
        return "Argos Translate, offline on this machine"


# -- what the case keeps: translations.json ----------------------------------------------------------------------------


def _read(client_dir: str | Path) -> dict[str, Any]:
    path = Path(client_dir) / STATE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "documents": {}}


def _write(client_dir: str | Path, data: dict[str, Any]) -> None:
    path = Path(client_dir) / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def state_of(client_dir: str | Path, doc_id: str) -> dict[str, Any]:
    return dict((_read(client_dir).get("documents") or {}).get(doc_id) or {})


def _save_state(client_dir: str | Path, doc_id: str, state: dict[str, Any]) -> None:
    data = _read(client_dir)
    data.setdefault("documents", {})[doc_id] = state
    _write(client_dir, data)
    events.record("translations", "saved", "Saved the translation of a document", case_dir=client_dir, version=data.get("version") or 1)


def pdf_path(client_dir: str | Path, doc_id: str) -> Path:
    return Path(client_dir) / FOLDER / f"translation-{doc_id}.pdf"


def _by(who: str) -> dict[str, str]:
    return {"who": who.strip(), "at": _now()}


def _take_back(state: dict[str, Any], who: str, why: str) -> None:
    """A signature taken back stays in the history with who signed, when, and who took it back, when and why."""
    if state.get("signed"):
        state.setdefault("history", []).append({**state["signed"], "taken_back": _by(who), "why": why})
    state["signed"] = None


def _signed(state: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
    """The signature, when it was given for the person the record names now (a signature never stands for another person)."""
    signed = state.get("signed")
    return signed if signed and signed.get("person") == record.get("person") else None


def _need_who(who: str) -> None:
    if not (who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")


def _record(client_dir: str | Path, doc_id: str) -> dict[str, Any]:
    import documents

    record = next((r for r in documents.load(client_dir)["documents"] if r["id"] == doc_id), None)
    if record is None:
        raise LookupError("unknown document")
    return record


def _foreign_record(client_dir: str | Path, doc_id: str) -> dict[str, Any]:
    record = _record(client_dir, doc_id)
    if not needs(record):
        raise ValueError("This document doesn't need a translation: it is in English, or it is not one of the documents a filing has translated.")
    return record


# -- the offline translator ------------------------------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Upper case with the accents taken off, one character for each character (so a position in it is a position in the text)."""
    return "".join((unicodedata.normalize("NFD", c)[:1] or c).upper() for c in text)


def _keep(fields: list[tuple[str, Any]]) -> list[str]:
    """The names and places the extractors found: a translator leaves them as written ("EXEMPLO" is a surname, not "example"), so
    the machine is never shown them. Whole names first, then each word of them (a name split over two lines)."""
    whole, words = [], []
    for key, value in fields:
        if key.endswith(("name", "names", "city", "birthplace", "naturalidade", "place", "residence")) or ".birthplace" in key:
            for part in str(value).split("|"):
                part = _fold(re.sub(r"\s+", " ", part).strip())
                if len(part) >= 3:
                    whole.append(part)
                words += [w for w in re.findall(r"[A-Z]{3,}", part) if w not in _COMMON]
    return sorted(dict.fromkeys(whole + words), key=len, reverse=True)


_COMMON = {"THE", "AND", "DEL", "DAS", "DOS", "BRASIL", "BRAZIL", "STATE"}


def _masked(line: str, keep: list[str]) -> tuple[str, dict[str, str]]:
    """The line with each kept name or place replaced by a token the translator passes through (Zq1, Zq2...); {token: as written}."""
    folded, spans = _fold(line), []
    for item in keep:
        for m in re.finditer(r"(?<![A-Z0-9])" + re.escape(item) + r"(?![A-Z0-9])", folded):
            if not any(m.start() < e and s < m.end() for s, e in spans):
                spans.append((m.start(), m.end()))
    out, tokens, last = [], {}, 0
    for n, (s, e) in enumerate(sorted(spans), 1):
        out += [line[last:s], f"Zq{n}"]
        tokens[f"Zq{n}"] = line[s:e]
        last = e
    return "".join(out + [line[last:]]), tokens


def _translate_line(line: str, lang: str, keep: list[str]) -> str:
    from portal import questions

    masked, tokens = _masked(line, keep)
    if not tokens:
        return questions.translate_to_english(line, lang)
    english = questions.translate_to_english(masked, lang)
    if english and set(re.findall(r"Zq\d+", english)) >= set(tokens):
        return re.sub(r"Zq\d+", lambda m: tokens.get(m.group(0), m.group(0)), english)  # one pass: Zq10 is never Zq1 and a 0
    return ""  # the translator lost a name: the line stays as written (counted as not translated), never retranslated with the name unprotected


def machine(text: str, lang: str, keep: list[str] | None = None) -> dict[str, Any]:
    """The text in English, line by line (a line stays beside its original line): {"status", "text", "problem"}.
    status "machine" when it was translated; "needs_translator" (Haitian Creole: no model exists), "missing_pair" (the
    model for this language isn't installed on this machine), "empty" (no text was read from the document) or "failed"
    (the model was asked and gave nothing) otherwise -- always with the reason in words. keep: names and places left as written."""
    from classify import translate

    name = _words(lang)
    if lang == "ht":
        return {"status": "needs_translator", "text": "", "problem": CREOLE_PROBLEM}
    if lang not in translate.installed_language_codes():
        return {"status": "missing_pair", "text": "",
                "problem": f"The offline translator on this machine has no {name} to English model, so nothing was translated. "
                           f"Ask whoever runs the system to install it, or have the translator type the translation below."}
    if not (text or "").strip():
        return {"status": "empty", "text": "", "problem": EMPTY_PROBLEM}
    out, tried, failed = [], 0, 0
    for line in text.splitlines():
        if not re.search(r"[^\W\d_]", line):  # a number, a date or a rule line: nothing to translate
            out.append(line.strip())
            continue
        tried += 1
        english = _translate_line(line.strip(), lang, keep or [])
        if not english:
            failed += 1
        out.append(english or line.strip())
    if tried and failed == tried:
        return {"status": "failed", "text": "", "problem": f"The offline translator gave nothing back for this {name} document. "
                                                           "The translator types the translation below."}
    note = (f"{failed} line{'s' if failed != 1 else ''} could not be translated and {'are' if failed != 1 else 'is'} shown as written: "
            "the translator checks them against the original.") if failed else ""
    return {"status": "machine", "text": "\n".join(out).strip(), "problem": note}


# -- making the translation, choosing the translator, signing ---------------------------------------------------------------


def make(client_dir: str | Path, doc_id: str, who: str, again: bool = False, lock=None) -> dict[str, Any]:
    """The English text (kept in the record, made once), then the draft PDF. again: translate afresh (a machine draft only:
    a text the translator typed or corrected is never overwritten). lock: held while the case's files are written, never while
    the translator works (a document can take a while)."""
    import contextlib

    import documents

    _need_who(who)
    record = _foreign_record(client_dir, doc_id)
    state = state_of(client_dir, doc_id)
    if record.get("translated") and state.get("status") == "typed" and again:
        raise ValueError("A translation the translator typed or corrected is saved: edit it instead of translating again.")
    if not record.get("translated") or again:
        made = machine(record.get("text") or "", foreign_language(record), _keep(_fields(client_dir, record.get("doc_ids") or record.get("files") or [])))
        with lock or contextlib.nullcontext():
            if made["text"]:
                documents.set_translated(client_dir, doc_id, made["text"], who)
            state = {**state_of(client_dir, doc_id), "language": foreign_language(record), "status": made["status"], "problem": made["problem"],
                     "engine": engine(), "made_by": _by(who)}
            _take_back(state, who, "the translation was made again")
            _save_state(client_dir, doc_id, state)
    render(client_dir, doc_id)
    return entry(client_dir, doc_id)


def set_text(client_dir: str | Path, doc_id: str, text: str, who: str) -> dict[str, Any]:
    """The translator's own English text (typed, or the machine draft corrected): it replaces the draft, and a signature already
    recorded is taken back -- the translator signs the text as it now stands."""
    import documents

    _need_who(who)
    record = _foreign_record(client_dir, doc_id)
    text = (text or "").strip()
    if not text:
        raise ValueError("Type the English translation first.")
    if len(text) > TEXT_MAX:
        raise ValueError(f"The translation is longer than {TEXT_MAX:,} characters: save it in parts, or ask for help.")
    state = state_of(client_dir, doc_id)
    # typed from nothing (no machine text: Haitian Creole, a missing model), or a draft corrected: each person is kept as they were
    key = "corrected_by" if record.get("translated") else "typed_by"
    state |= {"language": foreign_language(record), "status": "typed", "problem": "", key: _by(who)}
    _take_back(state, who, "the translation text was changed")
    documents.set_translated(client_dir, doc_id, text, who)
    _save_state(client_dir, doc_id, state)
    render(client_dir, doc_id)
    return entry(client_dir, doc_id)


def set_translator(client_dir: str | Path, doc_id: str, translator_id: str, who: str) -> dict[str, Any]:
    """The attorney picks the translator who will sign this document's certificate: one of Settings' translators whose languages
    include the document's. The certificate keeps the name and statement as they are now."""
    _need_who(who)
    record = _foreign_record(client_dir, doc_id)
    row = next((t for t in translators() if t["id"] == translator_id), None)
    if row is None:
        raise ValueError("Choose a translator from the list (the Settings page keeps it).")
    if foreign_language(record) not in row["languages"]:
        raise ValueError(f"{row['name']} is not set up for {_words(foreign_language(record))}: add the language in Settings or choose another translator.")
    state = state_of(client_dir, doc_id)
    state |= {"language": foreign_language(record), "translator": {k: row.get(k) for k in ("id", "name", "languages", "kind", "competence", "organization", "address")},
              "translator_set_by": _by(who)}
    _take_back(state, who, "another translator was chosen")
    _save_state(client_dir, doc_id, state)
    if record.get("translated"):
        render(client_dir, doc_id)
    return entry(client_dir, doc_id)


def sign(client_dir: str | Path, doc_id: str, who: str, today: date | None = None) -> dict[str, Any]:
    """Records that the named translator signed: who pressed it and when. The PDF is drawn again without the DRAFT marks and
    carrying "signed MM/DD/YYYY"."""
    _need_who(who)
    record = _foreign_record(client_dir, doc_id)
    state = state_of(client_dir, doc_id)
    if not record.get("translated"):
        raise ValueError("There is no English translation yet: make it, or type it, first.")
    if not state.get("translator"):
        raise ValueError("The attorney chooses the translator first: the certificate carries the translator's name.")
    if (record.get("person") or "unknown") == "unknown":
        raise ValueError("Set whose document this is first (on the Documents page): the certificate names the person.")
    day = today or clock.today()
    state["signed"] = {"by": who.strip(), "at": _now(), "date": day.strftime("%m/%d/%Y"), "translator": state["translator"]["name"],
                       "person": record["person"]}
    _save_state(client_dir, doc_id, state)
    render(client_dir, doc_id)
    return entry(client_dir, doc_id)


def reopen(client_dir: str | Path, doc_id: str, who: str) -> dict[str, Any]:
    """Takes the signature back (a correction is needed): the translation and the certificate are DRAFT again."""
    _need_who(who)
    _foreign_record(client_dir, doc_id)
    state = state_of(client_dir, doc_id)
    if state.get("signed"):
        _take_back(state, who, "taken back by hand")
        _save_state(client_dir, doc_id, state)
        render(client_dir, doc_id)
    return entry(client_dir, doc_id)


def person_changed(client_dir: str | Path, doc_id: str, who: str) -> None:
    """A reviewer changed whose document this is (documents.set_person): the certificate names the person, so a signature given for
    the old one is taken back (kept in the history) and the draft is drawn again."""
    record = _record(client_dir, doc_id)
    if not needs(record):
        return
    state = state_of(client_dir, doc_id)
    if state.get("signed") and state["signed"].get("person") != record.get("person"):
        _take_back(state, who, "the person the document is about was changed")
        _save_state(client_dir, doc_id, state)
    if (record.get("translated") or "").strip():
        render(client_dir, doc_id)


# -- the PDF -------------------------------------------------------------------------------------------------------------------


def _sheet():
    from review.bundle import _Sheet

    return _Sheet()


def _draft_mark(p) -> None:
    p.ops[:0] = ["q 0.88 g BT /F2 110 Tf 0.766 0.643 -0.643 0.766 120 190 Tm (DRAFT) Tj ET Q"]


def _fields(client_dir: str | Path, doc_ids: list[str]) -> list[tuple[str, Any]]:
    """What the extractors found in this document: (fact key, value), from the case's fact graph."""
    path = Path(client_dir) / "fact_graph.json"
    if not path.exists():
        return []
    from factgraph import FactGraph

    wanted, out, seen = set(doc_ids), [], set()
    for key, fact in FactGraph.load(path).all_facts().items():
        if key in seen or key.startswith(("folder.", "questionnaire.")):
            continue
        for s in fact.sources:
            if s.doc_id in wanted and s.normalized_value not in (None, ""):
                out.append((key, s.normalized_value))
                seen.add(key)
                break
    return out


def _name_found(fields: list[tuple[str, Any]]) -> str:
    found = dict(fields)
    for key in ("applicant.birth_certificate_name", "applicant.marriage_party_names"):
        if found.get(key):
            return str(found[key]).replace(" | ", " and ")
    given, family = found.get("applicant.given_name"), found.get("applicant.family_name")
    return f"{given} {family}" if given and family else ""


def _person_line(record: dict[str, Any], fields: list[tuple[str, Any]]) -> str:
    name, person = _name_found(fields), record.get("person") or "unknown"
    if person == "unknown":  # a draft only: sign() refuses until a reviewer says whose it is
        return name or "[whose document this is: not set yet]"
    return f"{name} ({person_words(person)})" if name else person_words(person)


def _business(state: dict[str, Any]) -> tuple[str, str]:
    """(the firm or business, the address) the certificate names for the translator: the firm's own name for one who works for it."""
    t = state.get("translator") or {}
    org = t.get("organization") or ""
    if not org and t.get("kind") == "firm":
        try:
            import offices

            first = offices.offices()[0]
            org = first["values"].get("firm.business_name") or first["name"]
        except Exception:  # noqa: BLE001 -- the certificate is made without the firm's name rather than not at all
            org = ""
    return org, t.get("address") or ""


def render(client_dir: str | Path, doc_id: str) -> Path | None:
    """Writes translations/translation-<id>.pdf from the record and its state; None when there is no English text yet."""
    from review.bundle import _jpeg, _Scans, _wrap

    client_dir = Path(client_dir)
    record = _record(client_dir, doc_id)
    state = state_of(client_dir, doc_id)
    text = (record.get("translated") or "").strip()
    if not text:
        return None
    signed = _signed(state, record)
    fields = _fields(client_dir, record.get("doc_ids") or record.get("files") or [])
    document, language = documents_name(record), _words(foreign_language(record))
    person = _person_line(record, fields)
    pages = record.get("pages") or [1]
    first_doc = (record.get("doc_ids") or record.get("files") or [""])[0]
    images = _Scans(client_dir).images(first_doc.partition("#")[0])
    if images is None:
        log.warning("translation: the pages of %s could not be drawn for %s: the sheet says so", first_doc, client_dir.name)

    col = (WIDTH - 2 * MARGIN - 14) / 2
    top, bottom, leading, size = HEIGHT - 104, 66, 12, 9
    capacity = int((top - 20 - bottom) / leading)
    lines = _wrap(text, size, col)
    chunks = [lines[i:i + capacity] for i in range(0, len(lines), capacity)] or [[]]
    sheets = []
    for n in range(max(len(pages), len(chunks))):
        p = _sheet()
        if not signed:
            _draft_mark(p)
        p.text(MARGIN, HEIGHT - 44, "English translation", "F2", 13)
        p.text(MARGIN, HEIGHT - 60, f"Document: {document}, in {language}", "F3", 9)
        p.text(MARGIN, HEIGHT - 72, f"Person: {person}", "F3", 9)
        p.text(MARGIN, HEIGHT - 84, f"Translated from {language} into English", "F3", 9)
        p.line(MARGIN, HEIGHT - 92, WIDTH - MARGIN, HEIGHT - 92, 0.5)
        right = MARGIN + col + 14
        if n < len(pages):
            p.text(MARGIN, top, f"Original, page {n + 1} of {len(pages)}", "F2", 8)
            page_image = images[pages[n] - 1] if images is not None and 0 < pages[n] <= len(images) else None
            if page_image is not None:
                jpeg, size_px, w, h = _jpeg(page_image, col, top - 18 - bottom)
                p.image(jpeg, size_px, MARGIN, top - 18 - h, w, h)
            else:
                p.text(MARGIN, top - 18, "The original page could not be drawn here: attach the original.", "F3", 8)
        else:
            p.text(MARGIN, top, "Original (continued on the pages before)", "F2", 8)
        p.text(right, top, "English translation" + (" (continued)" if n else ""), "F2", 8)
        for i, line in enumerate(chunks[n] if n < len(chunks) else []):
            p.text(right, top - 20 - i * leading, line, "F3", size)
        sheets.append(p)
    sheets.append(_certificate(client_dir, record, state, document, language, person, len(pages), signed))
    for i, p in enumerate(sheets):
        last = i == len(sheets) - 1
        foot = (f"Signed {signed['date']} by {state['translator']['name']}. English translation and certificate of translation (8 CFR 103.2(b)(3))."
                if signed else DRAFT_CERTIFICATE if last else DRAFT_TYPED if state.get("status") == "typed" else DRAFT_MACHINE)
        p.text(MARGIN, 38, foot, "F2", 8)
        p.text(WIDTH - MARGIN - 56, 26, f"Page {i + 1} of {len(sheets)}", "F3", 7)
    writer = PdfWriter()
    for p in sheets:
        writer.add_page(p.to_page(writer))
    writer.add_metadata({"/Title": f"English translation and certificate: {document}", "/Subject": "Translation under 8 CFR 103.2(b)(3)"})
    buf = io.BytesIO()
    writer.write(buf)
    out = pdf_path(client_dir, doc_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return out


def documents_name(record: dict[str, Any]) -> str:
    import documents

    return documents.name(record["type"])


def _certificate(client_dir: Path, record: dict[str, Any], state: dict[str, Any], document: str, language: str, person: str, pages: int,
                 signed: dict[str, Any] | None):
    """The one page the translator signs: the regulation's two certifications, the document, and the translator."""
    from review.bundle import _wrap

    p = _sheet()
    if not signed:
        _draft_mark(p)
    t = state.get("translator") or {}
    name = t.get("name") or "[translator not chosen yet]"
    p.text(MARGIN, HEIGHT - 60, "CERTIFICATE OF TRANSLATION", "F2", 16)
    p.text(MARGIN, HEIGHT - 76, "Under 8 CFR 103.2(b)(3), for submission to U.S. Citizenship and Immigration Services", "F3", 9)
    p.line(MARGIN, HEIGHT - 84, WIDTH - MARGIN, HEIGHT - 84, 0.5)
    y = HEIGHT - 116
    sentence = CERTIFICATE_WORDING.format(name=name, language=language, document=document.lower(), person=person)
    for line in _wrap(sentence, 12, WIDTH - 2 * MARGIN):
        p.text(MARGIN, y, line, "F3", 12)
        y -= 17
    y -= 14
    rows = [("Document translated", document), ("Language of the original", language), ("Pages in the original", str(pages)),
            ("Whose", person)]
    if record.get("issued"):
        rows.append(("Date on the document", f"{record['issued'][5:7]}/{record['issued'][8:10]}/{record['issued'][:4]}"))
    for label, value in rows:
        p.text(MARGIN, y, label, "F2", 10)
        p.text(MARGIN + 170, y, value, "F3", 10)
        y -= 16
    y -= 18
    p.text(MARGIN, y, "Translator", "F2", 11)
    y -= 18
    org, address = _business(state)
    for label, value in (("Name", t.get("name") or "[translator not chosen yet]"), ("Languages", ", ".join(_words(c) for c in t.get("languages") or [])),
                         ("Firm or business", org), ("Address", address)):
        if value:
            p.text(MARGIN, y, label, "F2", 10)
            p.text(MARGIN + 170, y, value, "F3", 10)
            y -= 16
    if t.get("competence"):
        y -= 6
        p.text(MARGIN, y, "Statement of competence", "F2", 10)
        y -= 14
        for line in _wrap(t["competence"], 10, WIDTH - 2 * MARGIN):
            p.text(MARGIN, y, line, "F3", 10)
            y -= 13
    y -= 40
    p.line(MARGIN, y, MARGIN + 270, y)
    p.text(MARGIN, y - 12, "Translator's signature", "F3", 8)
    p.line(MARGIN + 320, y, WIDTH - MARGIN, y)
    if signed:
        p.text(MARGIN + 320, y + 4, f"Signed {signed['date']}", "F3", 10)
    p.text(MARGIN + 320, y - 12, "Date signed (MM/DD/YYYY)", "F3", 8)
    return p


# -- what the packet and the screens ask -------------------------------------------------------------------------------------


def _own_translation(files: list[dict[str, Any]]) -> bool:
    """The exhibit already holds a translation the folder brought (a translator's certificate, a file named for it)."""
    return any(not f.get("generated") and (f["type"] in ("translation_certification", "certified_translation") or re.search(r"transl|tradu", f["doc"], re.I))
               for f in files)


def eligible(language: str) -> list[dict[str, Any]]:
    """The Settings translators who translate this language into English: {id, name, kind_name}."""
    import settings

    return [{"id": t["id"], "name": t["name"], "kind_name": settings.TRANSLATOR_KINDS.get(t["kind"], t["kind"])} for t in translators() if language in t["languages"]]


def entry(client_dir: str | Path, doc_id: str, covered: bool = False, record: dict[str, Any] | None = None) -> dict[str, Any]:
    """One foreign document as the packet page shows it: state (needed, draft, signed, human, covered), the English text, the
    translator, who did what, and why a translation couldn't be made."""
    from classify import translate

    record = record or _record(client_dir, doc_id)
    state = state_of(client_dir, doc_id)
    lang = foreign_language(record) or record.get("language") or "unknown"
    text = (record.get("translated") or "").strip()
    problem = state.get("problem") or ""
    if not text and lang == "ht":
        problem = problem or CREOLE_PROBLEM
    elif not text and lang in FOREIGN and lang not in translate.installed_language_codes():
        problem = problem or machine("", lang)["problem"]
    if covered:
        shown = "covered"
    elif _signed(state, record) and text:
        shown = "signed"
    elif text:
        shown = "draft"
    elif problem:
        shown = "human"
    else:
        shown = "needed"
    return {"id": doc_id, "name": documents_name(record), "type": record["type"], "person": person_words(record.get("person") or "unknown"),
            "language": lang, "language_name": _words(lang), "state": shown, "problem": problem, "text": text,
            "how": state.get("status") or "", "made_by": state.get("made_by"), "typed_by": state.get("typed_by"), "corrected_by": state.get("corrected_by"),
            "engine": state.get("engine") if state.get("status") == "machine" else None,
            "translator": state.get("translator"), "translator_set_by": state.get("translator_set_by"),
            "translators": eligible(lang), "signed": _signed(state, record), "history": state.get("history") or [], "pdf": pdf_path(client_dir, doc_id).exists()}


def foreign_files(client_dir: str | Path, exhibits: list[dict[str, Any]], records: dict[str, dict[str, Any]]) -> list[tuple[dict, dict, dict]]:
    """(exhibit, the file entry, its record) for each foreign-language document in the packet's exhibits, once each."""
    out, seen = [], set()
    for ex in exhibits:
        for f in ex["files"]:
            r = records.get(f["doc"])
            if r and not f.get("generated") and needs(r) and r["id"] not in seen:
                seen.add(r["id"])
                out.append((ex, f, r))
    return out


def status(client_dir: str | Path, exhibits: list[dict[str, Any]], records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The packet page's translations: one entry per foreign-language document in the exhibits (records: doc id -> record)."""
    return [entry(client_dir, r["id"], covered=_own_translation(ex["files"]) and not _signed(state_of(client_dir, r["id"]), r), record=r)
            for ex, _f, r in foreign_files(client_dir, exhibits, records)]


def signed_entries(client_dir: str | Path, files: list[dict[str, Any]], records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The signed translation PDFs that go in an exhibit beside the originals they translate (packet.plan): file entries the packet
    prints as they are. A draft never goes in."""
    out, seen = [], set()
    for f in files:
        r = records.get(f["doc"])
        if not r or f.get("generated") or not needs(r) or r["id"] in seen:
            continue
        seen.add(r["id"])
        state, path = state_of(client_dir, r["id"]), pdf_path(client_dir, r["id"])
        if _signed(state, r) and (r.get("translated") or "").strip() and path.is_file():
            out.append({"doc": path.name, "type": "certified_translation", "path": str(path), "pages": None, "generated": True,
                        "translation_of": f["doc"], "label": f"English translation and translator's certificate: {documents_name(r)}",
                        "locked": True})
    return out


def checklist(client_dir: str | Path, exhibits: list[dict[str, Any]], records: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """The "before you mail it" lines for the foreign documents the exhibits hold: "type" says which kind of document, so the packet's
    own generic line for it can give way. A document the folder already has translated (its own certified translation in the exhibit)
    asks for nothing."""
    items = []
    for ex, _f, r in foreign_files(client_dir, exhibits, records):
        lang = _words(foreign_language(r))
        if _own_translation(ex["files"]):
            continue
        who = _name_found(_fields(client_dir, r.get("doc_ids") or r.get("files") or []))
        name = f"{documents_name(r).lower()} of {who}" if who else f"{documents_name(r).lower()} ({person_words(r.get('person') or 'unknown')})"
        e = entry(client_dir, r["id"], record=r)
        if e["state"] == "signed":
            items.append({"kind": "sign", "type": r["type"], "text": f"The translator signs the certificate of translation for the {name} "
                          f"({lang}) in ink, on the last page of its translation in the packet. Signed on the screen {e['signed']['date']}."})
        elif e["state"] == "draft":
            items.append({"kind": "missing", "type": r["type"], "text": f"The English translation of the {name} ({lang}) is a DRAFT: the translator "
                          "checks it against the original, and a signature is recorded on the filing packet page. Only a signed translation goes in the packet."})
        elif e["state"] == "human":
            items.append({"kind": "missing", "type": r["type"], "text": f"The {name} ({lang}) needs a human translator: {e['problem']}"})
        else:
            items.append({"kind": "missing", "type": r["type"], "text": f"No English translation of the {name} ({lang}) yet. Make the translation on the filing "
                          "packet page, have the translator check and sign it, and rebuild."})
    return items


# -- the summary a reviewer reads without the language ----------------------------------------------------------------------------

_LABELS = {
    "applicant.birth_certificate_name": "Name on it", "applicant.dob": "Born", "applicant.birth_city": "Place of birth", "applicant.birth_state": "State",
    "applicant.country_of_birth": "Country", "applicant.sex": "Sex", "applicant.birth_cert.registration_city": "Registered in",
    "applicant.birth_cert.naturalidade": "Place of origin", "applicant.birth_cert.grandparents": "Grandparents named",
    "applicant.marriage_party_names": "Spouses named", "applicant.marriage_date": "Married on", "applicant.marriage_place": "Place of the marriage",
    "applicant.marital_status": "Marital status",
}
_ORDER = list(_LABELS)


def _shown(value: Any) -> str:
    v = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)}" if m else v.replace(" | ", " and ")


def summary(record: dict[str, Any], fields: list[tuple[str, Any]]) -> str:
    """One paragraph, from the extractor's fields only: what the document is, whose, and the names, places and dates found. No fields:
    "no reader for this type"."""
    lang = _words(foreign_language(record) or (record.get("translation_kept") or {}).get("language") or record.get("language") or "unknown")
    head = f"{documents_name(record)} in {lang}, {person_words(record.get('person') or 'unknown')}."
    if not fields:
        return f"{head} No reader for this type of document: nothing was read from it, so the translator reads the original."
    parts, parents = [], []
    found = dict(fields)
    for key in _ORDER:
        if key in found:
            parts.append(f"{_LABELS[key]}: {_shown(found[key])}")
    for key, value in fields:
        if re.fullmatch(r"applicant\.birth_cert\.[a-z_]+_name", key) and key != "applicant.birth_certificate_name":
            parents.append(_shown(value))
        elif key not in _LABELS and not key.startswith("applicant.birth_cert.") and len(parts) < 10:
            label = key.rsplit(".", 1)[-1].replace("_", " ")
            parts.append(f"{label[:1].upper()}{label[1:]}: {_shown(value)}")
    if parents:
        parts.insert(min(len(parts), 5), "Parents named: " + "; ".join(dict.fromkeys(parents)))
    return f"{head} {'. '.join(parts)}. Read from the document by the system, not by a person: the translator checks every name, date and number against the original."


_SUMMARIES: dict[str, tuple[tuple, dict[str, dict[str, str]]]] = {}  # case folder -> (its files' times, the summaries): a card is opened often


def _stamp(client_dir: Path) -> tuple:
    return tuple((client_dir / n).stat().st_mtime_ns if (client_dir / n).exists() else 0 for n in ("documents.json", "fact_graph.json", "meta.json"))


def summaries(client_dir: str | Path) -> dict[str, dict[str, str]]:
    """{doc id (as meta.json knows it): {"id", "language", "language_name", "summary"}} for each foreign-language document in the case."""
    import documents

    client_dir = Path(client_dir)
    stamp = _stamp(client_dir)
    cached = _SUMMARIES.get(str(client_dir))
    if cached and cached[0] == stamp:
        return cached[1]
    out: dict[str, dict[str, str]] = {}
    if (client_dir / documents.FILE).exists():  # a case processed before the record existed has no language to go by
        for r in (documents.read(client_dir) or {}).get("documents") or []:
            lang = foreign_language(r) or (r.get("translation_kept") or {}).get("language")  # a translation a person chose to drop still has its summary
            if lang in FOREIGN:
                ids = r.get("doc_ids") or r.get("files") or []
                text = summary(r, _fields(client_dir, ids))
                for d in ids:
                    out[d] = {"id": r["id"], "language": lang, "language_name": _words(lang), "summary": text}
    _SUMMARIES[str(client_dir)] = (stamp, out)
    return out
