"""Drafting with provenance: the client's declaration, and the cover letter's case paragraph (docs/design_plan.md Part 6).

The I-589, the U petition (I-918), the T application (I-914) and the VAWA self-petition (I-360) each need the client's own
signed statement (the packets' "declaration" exhibit: schemas/packets/i589.json, packet_u_visa.json, packet_i914.json,
packet_vawa.json). This module assembles it from what the client already said, and from nothing else.

THE RULE THAT KEEPS A MODEL FROM WRITING FACTS. Every paragraph of the draft is one of the client's own long answers,
verbatim, in the order the filing's questions ask them, tagged with the question it answers (its fact key), the date and
who recorded it, and its language. The draft adds no sentence of its own. Two things may change a paragraph's English,
and each is recorded:
  - the offline translator (Argos, through translation.machine and portal/questions.translate_to_english, the one
    translator call), for an answer not in English: marked "machine translation", DRAFT, until a person replaces it;
  - a person: an edit on the Declaration card, or a grammar suggestion the person accepts -- a review decision
    (review/state.record_decision) with who, when, the old text and the new, shown on the card and in the review bundle,
    and undone the same way.
A local model may only SUGGEST grammar corrections, only when an attorney has switched grammar smoothing on in Settings
(off by default), one paragraph at a time. Its suggestion is refused before anyone sees it unless it passes
smoothing_allowed(): it may add or take away "a", "an" and "the", change capitals and spacing and the full stop at the
very end, and nothing else -- every other word and punctuation mark stays exactly as the paragraph has it (the same form:
no tense or number change), in the same order and the same number of times; the numbers in the same order; no character
from outside the paragraph's own and plain ASCII. A suggestion that passes is shown beside the paragraph with its
word-by-word difference, and is never used until a person accepts it. The client's original words are never changed by
a model. A person may also leave a paragraph out (a recorded decision); every answer is in until someone does.

Each paragraph's language is read from its own words, line by line: lines in different languages make it "mixed", and an
answer too short to tell takes the language the client reads the portal in. Either way it is "not certain", and the
declaration cannot be marked final until a person confirms the language (a recorded decision).

The practice is the attorney's to approve before it is used on a real case: PRACTICE below is recorded like a rule's
approval (src/rules/approval.py, id PRACTICE_ID), and "Mark as the client's final" is refused until it is approved.
The marked text is kept (its hash); a change to any paragraph afterwards takes the mark back. The exhibit
(declarations/declaration-<filing>.pdf) goes in the packet's declaration exhibit once marked final, watermarked
DRAFT until a person records the date the client signed it (client_signed).

The cover letter's case paragraph (cover_paragraph): for these four filings only, the letter's opening has a
{case_paragraph} slot (schemas/cover_letters/*.json "intro"). Each sentence is fixed wording (DRAFT, listed in
docs/attorney_review.md) with values from the case's facts, each with its source; a sentence whose fact is missing is
left out, never written with a gap.

The signature formula is 28 U.S.C. 1746 (U.S. Code 2024 edition, govinfo.gov,
https://www.govinfo.gov/content/pkg/USCODE-2024-title28/html/USCODE-2024-title28-partV-chap115-sec1746.htm, read
10/02/2026): "(2) If executed within the United States, its territories, possessions, or commonwealths: 'I declare (or
certify, verify, or state) under penalty of perjury that the foregoing is true and correct. Executed on (date).
(Signature)'"; "(1) If executed without the United States", the same with "under the laws of the United States of
America" after "perjury".
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import re
import string
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import clock
import events
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

STATE = "declarations.json"
FOLDER = "declarations"
PRACTICE_ID = "PRACTICE:DRAFTING"
USC_1746 = ("28 U.S.C. 1746, https://www.govinfo.gov/content/pkg/USCODE-2024-title28/html/"
            "USCODE-2024-title28-partV-chap115-sec1746.htm (read 10/02/2026)")
DECLARE_IN_US = "I declare under penalty of perjury that the foregoing is true and correct."  # 28 U.S.C. 1746(2)
DECLARE_ABROAD = "I declare under penalty of perjury under the laws of the United States of America that the foregoing is true and correct."  # 1746(1)

# The practice the attorney approves (Keeping current, like a rule): the words recorded with the approval.
PRACTICE = ("Client declarations are assembled only from the client's own long answers to the filing's questions (typed from what "
            "the client said, or the client's own typed reply in the portal), word for word, in the order the questions ask them, "
            "each paragraph shown with the question it answers, when and by whom it was recorded, and its language. Nothing else "
            "is added. An answer not in English gets an English draft from the offline translator, marked as a machine translation "
            "until a person replaces it; the firm's certified translator checks the English before filing. When the language of an "
            "answer is not certain, a person confirms it before the declaration can be marked final. A person may leave a paragraph "
            "out. A local model may only suggest grammar corrections to the English of one paragraph, and only when an attorney "
            "has switched grammar smoothing on in Settings; a suggestion is shown only if it keeps every other word and punctuation "
            "mark of the paragraph exactly as written, in the same order, changing nothing but the words \"a\", \"an\" and \"the\", "
            "capital letters, spacing and the final full stop. A suggestion is never used until a person accepts it. Every edit, "
            "accepted suggestion, language confirmation and paragraph left out is recorded with who and when (an edit also with "
            "the old text and the new). Only an attorney marks a declaration as the client's final, and it stays marked DRAFT "
            "until a person records the date the client signed it.")
PRACTICE_SOURCE = ("The firm's own practice for drafting declarations, written for the attorney's approval: no statute or rule "
                   "requires it. The signature line is the form in 28 U.S.C. 1746")

# Each filing: the form it supports (for the heading) and who the client is on it (the cover letter's word).
FILINGS = {
    "i589": {"form": "Form I-589, Application for Asylum and for Withholding of Removal", "who": "applicant"},
    "u_visa": {"form": "Form I-918, Petition for U Nonimmigrant Status", "who": "petitioner"},
    "i914": {"form": "Form I-914, Application for T Nonimmigrant Status", "who": "applicant"},
    "vawa": {"form": "Form I-360, Petition for Amerasian, Widow(er), or Special Immigrant (VAWA self-petition)", "who": "self-petitioner"},
}
LANGUAGES = {"en": "English", "pt": "Portuguese", "es": "Spanish", "fr": "French", "ht": "Haitian Creole"}
LANGUAGE_NAMES = LANGUAGES | {"mixed": "More than one language"}
INCLUDE, EXCLUDE = "In the declaration", "Left out"  # a paragraph's place, a person's choice (a recorded decision)
SUGGESTION_NOTE = "Grammar suggestion accepted"  # the note of the decision that takes a model's suggestion
TEXT_MAX = 20000  # characters in one paragraph typed on the card


def _now() -> str:
    return clock.stamp()


def _by(who: str, role: str | None = None) -> dict[str, Any]:
    return {"who": who.strip(), **({"role": role} if role else {}), "at": _now()}


def _need_who(who: str) -> None:
    if not (who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _check_filing(filing: str) -> None:
    if filing not in FILINGS:
        raise ValueError("This filing has no client declaration here.")


# -- what the case keeps: declarations.json ------------------------------------------------------------------------------


def _read(client_dir: Path) -> dict[str, Any]:
    path = Path(client_dir) / STATE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "filings": {}}


def _state(client_dir: Path, filing: str) -> dict[str, Any]:
    return dict((_read(client_dir).get("filings") or {}).get(filing) or {})


def _save(client_dir: Path, filing: str, state: dict[str, Any]) -> None:
    data = _read(client_dir)
    data.setdefault("filings", {})[filing] = state
    path = Path(client_dir) / STATE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    import packet

    events.record("packet", "saved", f"Saved the client's declaration for {packet.filing_title(filing)}", case_dir=client_dir)


def pdf_path(client_dir: Path, filing: str) -> Path:
    return Path(client_dir) / FOLDER / f"declaration-{filing}.pdf"


# -- the questions and the client's answers ------------------------------------------------------------------------------


def questions(filing: str) -> list[tuple[str, str, str]]:
    """(fact key, the question as the panel asks it, its section) for each long answer that is the client's account, in the
    order the filing asks them: the I-589's Part B and Part C explanations (src/asylum.py); the client's own sections of the
    T, U and VAWA questions (src/t_visa.py, src/u_visa.py, src/vawa.py: "the client", multi-line)."""
    _check_filing(filing)
    if filing == "i589":
        import asylum

        return [(k, label, section) for k, label, section, spec, _r in asylum.QUESTIONS
                if spec.get("multiline") and section.startswith(("Part B", "Part C"))]
    import filing_questions

    return [(k, label, section) for k, label, section, spec, _r, who in filing_questions.questions(filing_questions.module(filing))
            if spec.get("multiline") and who == "the client"]


# The words that tell one language from another in a narrative (documents.language is tuned to certificates): counted with
# repeats; a language wins with at least 3 and half again as many as the next.
_MARKERS = {
    "pt": {"NAO", "EU", "MEU", "MINHA", "MEUS", "MINHAS", "COM", "UMA", "DO", "DA", "DOS", "DAS", "NA", "NAS", "ELE", "ELA", "ELES", "FOI",
           "MAS", "MUITO", "ESTAVA", "TINHA", "AO", "AOS", "SEU", "SUA", "VOCE", "ENTAO", "ISSO", "EM", "QUE", "PARA", "PORQUE", "QUANDO"},
    "es": {"YO", "MI", "MIS", "CON", "UNA", "EL", "LOS", "LAS", "DEL", "LA", "ELLA", "ELLOS", "FUE", "PERO", "MUY", "ESTABA", "TENIA",
           "AL", "SU", "SUS", "USTED", "ENTONCES", "ESO", "EN", "ME", "Y", "QUE", "PARA", "PORQUE", "CUANDO", "HAY"},
    "ht": {"MWEN", "NOU", "LI", "NAN", "AK", "POU", "KI", "GEN", "TE", "PA", "SOU", "YON", "LE", "SE", "KONSA", "PASKE", "MOUN", "YO"},
    "en": {"THE", "AND", "I", "MY", "ME", "WAS", "WERE", "THEY", "HE", "SHE", "TO", "OF", "IN", "THAT", "WITH", "FOR", "IT", "HAD", "WE",
           "OUR", "BECAUSE", "WHEN", "NOT", "THEM", "HIS", "HER"},
}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).upper()


def _detect(text: str) -> str | None:
    """The language a piece of text is surely in, from its own words; None when it says too little to tell."""
    words = re.findall(r"[A-Z]+", _fold(text))
    scores = sorted(((sum(1 for w in words if w in markers), lang) for lang, markers in _MARKERS.items()), reverse=True)
    (best, lang), (second, _) = scores[0], scores[1]
    if best >= 3 and best >= 1.5 * second:
        return lang
    import documents

    guess = documents.language(text)
    return guess if guess in LANGUAGES else None


def language(text: str, hint: str | None = None) -> tuple[str, str, bool]:
    """(language code, how it was decided, sure) for an answer. Each line and sentence is read on its own: when two of them are
    surely in different languages the answer is "mixed". Otherwise the whole answer's own words; when they say too little
    (a short answer: "Sim, fui preso duas vezes."), the language the client reads the portal in (hint), else English, and
    either way not sure: a person confirms it on the card before the declaration can be marked final."""
    pieces = [p for p in re.split(r"\n+|(?<=[.!?])\s+", text or "") if p.strip()]
    found = list(dict.fromkeys(lang for lang in (_detect(p) for p in pieces) if lang))
    if len(found) > 1:
        return "mixed", "the lines are in different languages (" + " and ".join(LANGUAGES[x] for x in found) + "): confirm", False
    whole = _detect(text)
    if whole:
        return whole, "read from the words", True
    if hint in LANGUAGES:
        return hint, "not certain: the language the client reads the portal in, confirm it", False
    return "en", "not certain: assumed English, confirm it", False


def _decisions(client_dir: Path) -> dict[str, dict[str, Any]]:
    from review.state import load_decisions

    return load_decisions(client_dir)


def _graph(client_dir: Path, filing: str):
    import filing_questions

    if filing == "i589":
        import asylum

        return asylum._graph(client_dir)
    return filing_questions.graph_for(filing, client_dir)


def _answer(graph, decisions: dict[str, dict[str, Any]], filing: str, key: str) -> dict[str, Any] | None:
    """The client's answer to one question, as recorded, with where it came from: a person typed it in the filing's questions
    (a review decision: who and when), the client typed it in the portal (the office's question: its words as written), or
    a document holds it. The text is the answer as it was given (never the capitals a form is filled in)."""
    fact = graph.get(key)
    if fact is None or fact.status != "resolved" or fact.value in (None, ""):
        return None
    full = graph.get(f"{key}_full")  # an I-589 explanation moved to its supplement sheet: the whole text (src/asylum.py)
    review = getattr(fact, "review", None)
    decision = decisions.get(f"{filing}:{key}")
    if review is not None and decision and key in (decision.get("values") or {}):
        text = str(decision["values"][key])
        return {"text": text, "kind": "reviewer", "by": decision.get("reviewer") or review.resolved_by, "role": decision.get("role"),
                "at": decision.get("at"), "words": f"Typed in the filing's questions by {decision.get('reviewer') or review.resolved_by}"}
    if review is not None:  # set on another card (the same fact): its reviewer, without the date of a filing question
        return {"text": str(fact.value), "kind": "reviewer", "by": review.resolved_by, "role": None, "at": review.resolved_at,
                "words": f"Set by {review.resolved_by} on a review card"}
    from portal.questions import OFFICE_DOC_ID

    for s in fact.sources:
        if s.doc_id == OFFICE_DOC_ID and str(s.raw_value or "").strip():
            return {"text": str(s.raw_value).strip(), "kind": "portal", "by": "the client", "role": None, "at": s.extracted_at,
                    "words": "Typed by the client in the portal, answering the office's question"}
    if full is not None and full.status == "resolved" and full.value:
        return {"text": str(full.value), "kind": "document", "by": None, "role": None, "at": None, "words": "From the case"}
    s = fact.sources[0] if fact.sources else None
    raw = str(s.raw_value).strip() if s is not None and str(s.raw_value or "").strip() else str(fact.value)
    return {"text": raw, "kind": "document", "by": None, "role": None, "at": s.extracted_at if s is not None else None,
            "words": f"Read from {s.doc_id}" if s is not None else "From the case", "doc": s.doc_id if s is not None else None}


# -- grammar smoothing: the model's one task, and the check that holds it to grammar ----------------------------------------

# The only words a suggestion may add or take away: the articles. Every other word -- "and", "or", "but", "then", every
# preposition, every form of "to be", "to have" and "to do", every pronoun and negation -- carries meaning in a sworn
# statement, and must stay exactly as the client's paragraph has it, in the same place.
GRAMMAR_WORDS = frozenset({"a", "an", "the"})
_TOKEN = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]")
_ENDS = frozenset(".!?")
_ALWAYS = frozenset(string.ascii_letters + string.digits + string.punctuation + " \n\t")


def _tokens(text: str) -> list[str]:
    """The paragraph as the check reads it: each word (exact, in lower case: never by its stem) and each punctuation mark, in
    order; the articles left out (the only words a suggestion may add or take away)."""
    out = [t.casefold() for t in _TOKEN.findall(unicodedata.normalize("NFC", text or ""))]
    return [t for t in out if t not in GRAMMAR_WORDS]


def smoothing_allowed(before: str, after: str) -> tuple[bool, str]:
    """The check a grammar suggestion must pass before anyone sees it (the module docstring): (shown, the reason in words when
    refused). The suggestion may add or take away "a", "an" and "the", change capitals and spacing, and add or take away the full
    stop at the very end. Every other word and punctuation mark of the paragraph stays exactly as it is (the same form: no tense,
    no plural), in the same order and the same number of times; the numbers stay in the same order; nothing in another script
    is added. Even then it is only a suggestion: a person accepts it or not."""
    after = (after or "").strip()
    if not after:
        return False, "the model gave nothing back"
    if re.search(r"\n\s*\n", after):
        return False, "the model split the paragraph in two"
    strange = sorted({c for c in after if c not in _ALWAYS and c not in (before or "")})
    if strange:
        return False, "the model used characters that are not in the paragraph: " + " ".join(strange[:6])
    old, new = _tokens(before), _tokens(after)
    if old and old[-1] in _ENDS:
        old = old[:-1]
    if new and new[-1] in _ENDS:
        new = new[:-1]
    if old == new:
        return True, ""
    if [t for t in old if t.isdigit()] != [t for t in new if t.isdigit()]:
        return False, "the model changed a number or a date"
    added = [t for t in new if t not in old and not t.isdigit()]
    if added:
        return False, "the model added words or marks that are not in the paragraph: " + " ".join(dict.fromkeys(added[:6]))
    dropped = [t for t in old if t not in new]
    if dropped:
        return False, "the model left out words or marks of the paragraph: " + " ".join(dict.fromkeys(dropped[:6]))
    if sorted(old) != sorted(new):
        return False, "the model repeated or left out a word or a punctuation mark the paragraph has"
    return False, "the model moved words or punctuation marks"


def diff(before: str, after: str) -> list[dict[str, str]]:
    """Word by word: [{"op": "same" | "removed" | "added", "text"}], for the card and the review bundle."""
    a, b = (before or "").split(), (after or "").split()
    out: list[dict[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append({"op": "same", "text": " ".join(a[i1:i2])})
            continue
        if i2 > i1:
            out.append({"op": "removed", "text": " ".join(a[i1:i2])})
        if j2 > j1:
            out.append({"op": "added", "text": " ".join(b[j1:j2])})
    return out


SMOOTH_PROMPT = ("Correct only the grammar, spelling and punctuation of this paragraph from a person's sworn statement. Do not add, "
                 "remove or change any fact, name, place, date, number or word of meaning; do not add any sentence. Keep the person's "
                 "own words wherever they are grammatical. Answer with the corrected paragraph only, nothing else.\n\nParagraph:\n")


MODEL_UNREACHABLE = "the local model could not be reached"  # what a card says when the model fails (never its name or an exception's)
MODEL_TIMEOUT = 300  # seconds for one answer from the local model


class ModelCutOff(RuntimeError):
    """The local model stopped at its length limit: its answer is cut off (said as that, never as "could not be reached")."""


def _timed_out(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) or "timed out" in str(exc).lower()


def local_model(prompt: str, num_predict: int, num_ctx: int = 8192, timeout: float = MODEL_TIMEOUT, retry_on_timeout: bool = True,
                raise_cut_off: bool = False) -> tuple[str, str]:
    """(the local model's answer to the prompt, the model's name): the firm's own Ollama on this machine (src/vision/ollama.py:
    nothing leaves the machine), temperature 0, `timeout` seconds. "" when the model answered with an error or ran out of room
    (raise_cut_off: ModelCutOff instead); raises when it cannot be reached. On WSL a refused connection is tried again through
    Windows (Ollama on the Windows side); a request that timed out is tried again only when retry_on_timeout (so a question's
    budget is never doubled). Grammar smoothing here and the questions about a case (src/case_questions.py) both call it."""
    from vision.ollama import DEFAULT_MODEL, DEFAULT_URL, _is_wsl, _post_http, _post_via_windows_curl

    model = os.environ.get("DRAFTING_MODEL", DEFAULT_MODEL)
    payload = {"model": model, "prompt": prompt, "stream": False,
               "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict}}
    endpoint = os.environ.get("OLLAMA_URL", DEFAULT_URL).rstrip("/") + "/api/generate"
    import jobs

    with jobs.gpu_lock():  # one use of the model at a time, whichever process asks: the job worker's and the overnight run's readings take the same turn
        try:
            result = _post_http(endpoint, payload, timeout)
        except OSError as exc:
            if not _is_wsl() or (_timed_out(exc) and not retry_on_timeout):
                raise
            result = _post_via_windows_curl(endpoint, payload, timeout, Path(".ocr_tmp"))
    if result.get("done_reason") == "length" and raise_cut_off:
        raise ModelCutOff("the answer was cut off at its length limit")
    if "error" in result or result.get("done_reason") == "length":
        return "", model
    return str(result.get("response") or "").strip(), model


def smooth_with_model(text: str) -> tuple[str, str]:
    """(the paragraph as a local model corrects it, the model's name). The one place smoothing calls the model (tests replace it)."""
    return local_model(SMOOTH_PROMPT + text, 64 + 3 * len(text.split()))


def smoothing_on() -> bool:
    """The attorney's switch on the Settings page ("Drafting and models"), off unless switched on."""
    import settings

    return settings.values("drafting").get("grammar_smoothing") == "on"


# -- the draft ----------------------------------------------------------------------------------------------------------


def _person(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {"who": record.get("reviewer") or record.get("who") or record.get("by"), "role": record.get("role"), "at": record.get("at"),
            "date": clock.us_date(record.get("at"))}


def _live(log: dict[str, dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    """The decision in force for one item of the decision log: set, not undone; None otherwise."""
    entry = log.get(item_id)
    return entry if entry and not entry.get("undone") and entry.get("action") == "set" else None


def declaration(client_dir: Path, filing: str, graph=None, client_language: str | None = None) -> dict[str, Any]:
    """The draft as it stands: {"filing", "form", "paragraphs", "hash", "machine", "missing_english", "unsure", "name", "abroad"}.
    Each paragraph: the question (id and label), the answer verbatim with its source, date and language (and whether a person
    still has to confirm the language), whether it is in the declaration (a person may leave one out), and its English (the
    original, the machine's draft, or a person's edit, an accepted grammar suggestion among them), with every edit on file and
    the grammar suggestion waiting for a person, if any. client_language: the language the client reads the portal in, used
    only when an answer says too little to tell. Only the paragraphs in the declaration are numbered, counted and hashed."""
    from review.state import _history, load_decision_log

    _check_filing(filing)
    client_dir = Path(client_dir)
    graph = graph if graph is not None else _graph(client_dir, filing)
    decisions = _decisions(client_dir)
    log = load_decision_log(client_dir)
    state = _state(client_dir, filing)
    english_cache, smoothed, confirmed = state.get("english") or {}, state.get("smoothing") or {}, state.get("languages") or {}
    smoothing = smoothing_on()
    paragraphs, n = [], 0
    for key, label, section in questions(filing):
        answer = _answer(graph, decisions, filing, key)
        if answer is None:
            continue
        text = answer["text"].strip()
        lang, how_lang, sure = language(text, client_language)
        mine = confirmed.get(key) or {}
        if mine.get("source") == _sha(text) and mine.get("language") in LANGUAGES:
            lang, sure = mine["language"], True
            how_lang = f"confirmed by {mine['by']['who']} on {clock.us_date(mine['by']['at'])}"
        made = english_cache.get(key) or {}
        if lang == "en":
            base, base_how, problem = text, "original", ""
        elif made.get("source") == _sha(text) and made.get("language") == lang and made.get("text"):
            base, base_how, problem = made["text"], "machine", made.get("problem") or ""
        else:
            base, base_how = "", "none"
            problem = ((made.get("problem") if made.get("source") == _sha(text) and made.get("language") == lang else "")
                       or ("Confirm the language first: the answer's lines are in different languages." if lang == "mixed"
                           else "No English draft yet: make it, or type the English."))
        chain = _history(log.get(f"declaration:{filing}:{key}"))
        live = _live(log, f"declaration:{filing}:{key}")
        included = (_live(log, f"declaration-include:{filing}:{key}") or {}).get("values", {}).get(f"declaration_include.{filing}.{key}", INCLUDE) == INCLUDE
        english, how = base, base_how
        if live is not None:
            english, how = str(next(iter(live["values"].values()))), "edited"
        record = smoothed.get(key)
        suggestion = None
        if record and live is None and base and record.get("before") == base and smoothing:
            suggestion = {k: record.get(k) for k in ("accepted", "reason", "after", "by", "diff", "declined")}
            # a suggestion identical to the paragraph changes nothing: the card says so and offers no Accept
            suggestion["same"] = (record.get("after") or "").strip() == (base or "").strip()
        if included:
            n += 1
        paragraphs.append({
            "id": key, "label": label, "section": section, "n": n if included else None, "included": included,
            "original": text, "language": lang, "language_name": LANGUAGE_NAMES.get(lang, lang), "language_how": how_lang, "language_sure": sure,
            "source": {"kind": answer["kind"], "words": answer["words"], "by": answer["by"], "role": answer.get("role"), "at": answer["at"],
                       "date": clock.us_date(answer["at"]), "doc": answer.get("doc")},
            "english": english, "english_how": how, "problem": "" if how == "edited" else problem,
            "engine": made.get("engine") if base_how == "machine" else None, "made_by": made.get("made_by") if base_how == "machine" else None,
            "edit": ({"who": live.get("reviewer"), "role": live.get("role"), "at": live.get("at"), "date": clock.us_date(live.get("at")),
                      "old": live.get("old"), "new": english, "suggestion": live.get("note") == SUGGESTION_NOTE,
                      "stale": live.get("old") not in (None, base)} if live is not None else None),
            "edits": [{"who": h.get("reviewer"), "role": h.get("role"), "at": h.get("at"), "date": clock.us_date(h.get("at")), "old": h.get("old"),
                       "new": next(iter((h.get("values") or {}).values()), None), "suggestion": h.get("note") == SUGGESTION_NOTE,
                       "undone": _person(h["undone"]) if h.get("undone") else None} for h in chain],
            "suggestion": suggestion,
            "smoothing": ({k: record.get(k) for k in ("accepted", "reason", "before", "after", "engine", "by", "diff", "declined")}
                          if record and record.get("before") in (base, (live or {}).get("old")) else None),
        })
    v = lambda k: (graph.get(k).value if graph.get(k) is not None and graph.get(k).status == "resolved" else None)  # noqa: E731
    name = " ".join(str(v(k)) for k in ("applicant.given_name", "applicant.middle_name", "applicant.family_name") if v(k))
    used = [p for p in paragraphs if p["included"]]
    return {"filing": filing, "form": FILINGS[filing]["form"], "paragraphs": paragraphs, "name": name.title() if name.isupper() else name,
            "abroad": v("vawa.in_us") == "No" if filing == "vawa" else False,
            "hash": _sha(json.dumps([[p["id"], p["original"], p["language"], p["english"]] for p in used], ensure_ascii=False)),
            "machine": sum(1 for p in used if p["english_how"] == "machine"),
            "missing_english": sum(1 for p in used if not p["english"].strip()),
            "unsure": sum(1 for p in used if not p["language_sure"]), "smoothing_on": smoothing}


# -- what people do on the card ----------------------------------------------------------------------------------------------


def make_english(client_dir: Path, filing: str, who: str, lock=None, client_language: str | None = None) -> dict[str, Any]:
    """The English drafts for the paragraphs not in English that have none (or whose answer or language changed since), with the
    offline translator: translation.machine (Haitian Creole has no model: said in words, for a person to type the English). A
    paragraph in more than one language waits for a person to confirm its language. lock: held only while the case's file is
    written, never while the translator works."""
    import contextlib

    import translation

    _need_who(who)
    draft = declaration(client_dir, filing, client_language=client_language)
    todo = [p for p in draft["paragraphs"] if p["language"] in LANGUAGES and p["language"] != "en" and p["english_how"] == "none"]
    if not todo:
        raise ValueError("Every paragraph already has its English (a paragraph in more than one language waits for its language to be confirmed).")
    made = {p["id"]: (p, translation.machine(p["original"], p["language"])) for p in todo}
    with lock or contextlib.nullcontext():
        state = _state(client_dir, filing)
        cache = state.setdefault("english", {})
        for key, (p, out) in made.items():
            cache[key] = {"source": _sha(p["original"]), "language": p["language"], "status": out["status"], "text": out["text"],
                          "problem": out["problem"], "engine": translation.engine() if out["text"] else None, "made_by": _by(who)}
        _save(client_dir, filing, state)
    return card(client_dir, filing, client_language)


def _paragraph(client_dir: Path, filing: str, key: str, client_language: str | None = None) -> dict[str, Any]:
    p = next((x for x in declaration(client_dir, filing, client_language=client_language)["paragraphs"] if x["id"] == key), None)
    if p is None:
        raise LookupError("No such paragraph in this declaration.")
    return p


def _record_english(client_dir: Path, filing: str, p: dict[str, Any], text: str, who: str, role: str | None, note: str) -> None:
    from review.state import record_decision

    fact = f"declaration.{filing}.{p['id']}"
    item = {"id": f"declaration:{filing}:{p['id']}", "kind": "declaration", "level": "review", "title": f"Declaration paragraph: {p['label']}",
            "group": "attorney", "actions": ["set", "blank"], "facts": [{"key": fact, "input": {"type": "text", "multiline": True}}]}
    record_decision(client_dir, item, {"action": "set", "values": {fact: text}, "reviewer": who, **({"role": role} if role else {}),
                                       "note": note, "old": p["english"]})


def edit(client_dir: Path, filing: str, key: str, text: str, who: str, role: str | None = None, client_language: str | None = None) -> dict[str, Any]:
    """A person's English for one paragraph: a review decision (who, when, the old text and the new), kept in the decision log."""
    _need_who(who)
    text = (text or "").strip()
    if not text:
        raise ValueError("Type the paragraph first (or use 'Back to the client's words').")
    if len(text) > TEXT_MAX:
        raise ValueError(f"The paragraph is longer than {TEXT_MAX:,} characters: split it, or ask for help.")
    p = _paragraph(client_dir, filing, key, client_language)
    if text == p["english"]:
        raise ValueError("Nothing changed in this paragraph.")
    _record_english(client_dir, filing, p, text, who, role, "Declaration paragraph edited")
    return card(client_dir, filing, client_language)


def revert(client_dir: Path, filing: str, key: str, who: str, role: str | None = None, client_language: str | None = None) -> dict[str, Any]:
    """Back to the client's words (or the machine's English): the edit is marked undone, kept on file with who and when."""
    from review.state import undo_decision

    _need_who(who)
    undo_decision(client_dir, f"declaration:{filing}:{key}", who, role)
    return card(client_dir, filing, client_language)


def accept_suggestion(client_dir: Path, filing: str, key: str, who: str, role: str | None = None, client_language: str | None = None) -> dict[str, Any]:
    """A person takes the model's grammar suggestion for one paragraph: recorded like an edit (who, when, the old text and the
    new), and undone the same way. Until then the declaration keeps the client's words."""
    _need_who(who)
    p = _paragraph(client_dir, filing, key, client_language)
    s = p.get("suggestion")
    if not s or not s.get("accepted") or s.get("declined") or s.get("same"):
        raise ValueError("There is no grammar suggestion to take for this paragraph." if not s or s.get("declined") or not s.get("accepted")
                         else "No change was suggested for this paragraph: there is nothing to accept.")
    _record_english(client_dir, filing, p, s["after"], who, role, SUGGESTION_NOTE)
    return card(client_dir, filing, client_language)


def decline_suggestion(client_dir: Path, filing: str, key: str, who: str, role: str | None = None, client_language: str | None = None) -> dict[str, Any]:
    """A person turns the grammar suggestion down: kept on file with who and when; the paragraph stays as it is."""
    _need_who(who)
    state = _state(client_dir, filing)
    record = (state.get("smoothing") or {}).get(key)
    if not record:
        raise ValueError("There is no grammar suggestion for this paragraph.")
    record["declined"] = _by(who, role)
    _save(client_dir, filing, state)
    return card(client_dir, filing, client_language)


def confirm_language(client_dir: Path, filing: str, key: str, lang: str, who: str, role: str | None = None, client_language: str | None = None) -> dict[str, Any]:
    """A person says which language an answer is in (one the system was not sure of, or one in more than one language): a
    review decision (who, when, the old and the new), held for the answer as it reads now."""
    from review.state import record_decision

    _need_who(who)
    if lang not in LANGUAGES:
        raise ValueError("Choose the language the answer is in.")
    p = _paragraph(client_dir, filing, key, client_language)
    fact = f"declaration_language.{filing}.{key}"
    item = {"id": f"declaration-language:{filing}:{key}", "kind": "declaration", "level": "review", "title": f"Declaration language: {p['label']}",
            "group": "attorney", "actions": ["set", "blank"], "facts": [{"key": fact, "input": {"type": "choice", "options": list(LANGUAGES)}}]}
    record_decision(client_dir, item, {"action": "set", "values": {fact: lang}, "reviewer": who, **({"role": role} if role else {}),
                                       "note": f"Language confirmed: {LANGUAGES[lang]}", "old": p["language"]})
    state = _state(client_dir, filing)
    state.setdefault("languages", {})[key] = {"source": _sha(p["original"]), "language": lang, "by": _by(who, role)}
    _save(client_dir, filing, state)
    return card(client_dir, filing, client_language)


def include(client_dir: Path, filing: str, key: str, keep: bool, who: str, role: str | None = None, client_language: str | None = None) -> dict[str, Any]:
    """A person puts a paragraph in the declaration or leaves it out (every answer is in until someone says otherwise): a review
    decision with who and when."""
    from review.state import record_decision

    _need_who(who)
    p = _paragraph(client_dir, filing, key, client_language)
    fact = f"declaration_include.{filing}.{key}"
    item = {"id": f"declaration-include:{filing}:{key}", "kind": "declaration", "level": "review", "title": f"Declaration paragraph kept or left out: {p['label']}",
            "group": "attorney", "actions": ["set", "blank"], "facts": [{"key": fact, "input": {"type": "choice", "options": [INCLUDE, EXCLUDE]}}]}
    record_decision(client_dir, item, {"action": "set", "values": {fact: INCLUDE if keep else EXCLUDE}, "reviewer": who, **({"role": role} if role else {}),
                                       "note": INCLUDE if keep else EXCLUDE, "old": INCLUDE if p["included"] else EXCLUDE})
    return card(client_dir, filing, client_language)


def smooth(client_dir: Path, filing: str, who: str, model=None, lock=None, client_language: str | None = None) -> dict[str, Any]:
    """Grammar suggestions, paragraph by paragraph (only when switched on in Settings). A suggestion that fails
    smoothing_allowed() is refused with the reason and never offered; one that passes waits for a person to accept or decline
    it. Nothing the model writes reaches the declaration by itself. A paragraph a person edited is never sent to the model.
    model: (text) -> (suggestion, model name); the local model by default."""
    import contextlib

    _need_who(who)
    if not smoothing_on():
        raise ValueError("Grammar smoothing is off. An attorney can switch it on in Settings (Drafting and models).")
    draft = declaration(client_dir, filing, client_language=client_language)
    state = _state(client_dir, filing)
    done = state.get("smoothing") or {}
    todo = [p for p in draft["paragraphs"] if p["english_how"] in ("original", "machine") and (done.get(p["id"]) or {}).get("before") != p["english"]]
    if not todo:
        raise ValueError("Nothing to check: every paragraph was checked already, was edited by a person, or has no English yet.")
    results = {}
    for p in todo:
        try:
            after, engine = (model or smooth_with_model)(p["english"])
        except Exception:  # noqa: BLE001 -- the model failing is said on the card, the paragraph stays as it was
            after, engine = "", MODEL_UNREACHABLE
        ok, why = smoothing_allowed(p["english"], after)
        results[p["id"]] = {"before": p["english"], "after": after[:TEXT_MAX], "accepted": ok, "reason": why, "engine": engine,
                            "by": _by(who), "diff": diff(p["english"], after) if after else []}
    with lock or contextlib.nullcontext():
        state = _state(client_dir, filing)
        state.setdefault("smoothing", {}).update(results)
        _save(client_dir, filing, state)
    return card(client_dir, filing, client_language)


def practice() -> dict[str, Any]:
    """The attorney's approval of the drafting practice (src/rules/approval.py): {state, by, at, text}."""
    from review.state import rule_info

    info = rule_info(PRACTICE_ID)
    return {"state": info["approval"]["state"], "text": info["approval_text"], "plain_text": info["plain_text"], "id": PRACTICE_ID}


def mark_final(client_dir: Path, filing: str, who: str, role: str | None = None) -> dict[str, Any]:
    """The attorney marks the declaration as the client's final: the text as it stands is kept (its hash and the paragraphs in
    it), and the exhibit is drawn, DRAFT until the client's signature is recorded."""
    _need_who(who)
    if role == "paralegal":
        raise PermissionError("Only an attorney marks a declaration as the client's final.")
    if practice()["state"] != "approved":
        raise ValueError("The attorney approves the drafting practice first (Keeping current, or the button on this card).")
    draft = declaration(client_dir, filing)
    used = [p for p in draft["paragraphs"] if p["included"]]
    if not used:
        raise ValueError("There is nothing to mark yet: the client's answers to the filing's questions make the declaration.")
    if draft["unsure"]:
        raise ValueError("Confirm the language of every paragraph marked 'Language not certain' first.")
    if draft["missing_english"]:
        raise ValueError("Some paragraphs have no English yet: make the English draft, or type it, first.")
    state = _state(client_dir, filing)
    _take_back(state, who, "marked final again")
    state["final"] = _by(who, role) | {"hash": draft["hash"], "date": clock.today().strftime("%m/%d/%Y"),
                                       "paragraphs": [{k: p[k] for k in ("id", "label", "original", "language", "english", "english_how")} for p in used]}
    state["signed"] = None
    _save(client_dir, filing, state)
    render(client_dir, filing)
    return card(client_dir, filing)


def _take_back(state: dict[str, Any], who: str, why: str) -> None:
    """A final mark (and a signature on it) taken back stays in the history with who, when and why."""
    if state.get("final"):
        state.setdefault("history", []).append({"final": {k: v for k, v in state["final"].items() if k != "paragraphs"},
                                                "signed": state.get("signed"), "taken_back": _by(who), "why": why})
    state["final"], state["signed"] = None, None


def take_back(client_dir: Path, filing: str, who: str, role: str | None = None) -> dict[str, Any]:
    _need_who(who)
    if role == "paralegal":
        raise PermissionError("Only an attorney takes back the client's final.")
    state = _state(client_dir, filing)
    if state.get("final"):
        _take_back(state, who, "taken back by hand")
        _save(client_dir, filing, state)
        path = pdf_path(client_dir, filing)
        if path.exists():
            path.unlink()
    return card(client_dir, filing)


def client_signed(client_dir: Path, filing: str, on: str, who: str, role: str | None = None) -> dict[str, Any]:
    """A person records the date the client signed the final declaration (YYYY-MM-DD from the date picker): the exhibit
    loses its DRAFT marks and says when."""
    _need_who(who)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(on or "")):
        raise ValueError("Choose the date the client signed, from the calendar.")
    try:
        day = date.fromisoformat(on)
    except ValueError:
        raise ValueError("Choose the date the client signed, from the calendar.") from None
    if day > clock.today():
        raise ValueError("The date the client signed can't be in the future.")
    state = _state(client_dir, filing)
    current = _final(client_dir, filing, state)
    if current is None:
        raise ValueError("Mark the declaration as the client's final first: the client signs the text the attorney marked.")
    state["signed"] = _by(who, role) | {"date": day.strftime("%m/%d/%Y"), "iso": on, "hash": state["final"]["hash"]}
    _save(client_dir, filing, state)
    render(client_dir, filing)
    return card(client_dir, filing)


def _final(client_dir: Path, filing: str, state: dict[str, Any] | None = None, draft: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The final mark when it still holds for the text as it stands now; None when there is none or the text changed since."""
    state = state if state is not None else _state(client_dir, filing)
    final = state.get("final")
    if not final:
        return None
    draft = draft or declaration(client_dir, filing)
    return final if final.get("hash") == draft["hash"] else None


def _signed(state: dict[str, Any]) -> dict[str, Any] | None:
    signed = state.get("signed")
    return signed if signed and state.get("final") and signed.get("hash") == state["final"].get("hash") else None


def current_pdf(client_dir: Path, filing: str) -> Path:
    """The declaration's PDF, only while the final mark still holds for the text as it stands (drawn again if it is missing);
    LookupError in plain words otherwise, so a copy of a changed declaration is never handed out."""
    _check_filing(filing)
    state = _state(client_dir, filing)
    if not state.get("final"):
        raise LookupError("The declaration is not marked as the client's final, so there is no declaration to open.")
    if _final(client_dir, filing, state) is None:
        raise LookupError("The declaration changed after it was marked final: the attorney marks it final again on the Declaration card.")
    path = pdf_path(client_dir, filing)
    return path if path.is_file() else render(client_dir, filing)


# -- the card the review app shows ------------------------------------------------------------------------------------------


def card(client_dir: Path, filing: str, client_language: str | None = None) -> dict[str, Any]:
    """The Declaration card: the draft, the practice's approval, the final mark and the signature, the cover paragraph.
    client_language: the language the client reads the portal in (the review app knows it), for an answer too short to tell."""
    client_dir = Path(client_dir)
    graph = _graph(client_dir, filing)
    draft = declaration(client_dir, filing, graph, client_language)
    state = _state(client_dir, filing)
    final = _final(client_dir, filing, state, declaration(client_dir, filing, graph))
    stale = bool(state.get("final")) and final is None
    signed = _signed(state) if final else None
    approval = practice()
    return draft | {
        "practice": approval, "final": {k: v for k, v in final.items() if k != "paragraphs"} if final else None, "changed_since_final": stale,
        "signed": signed, "pdf": pdf_path(client_dir, filing).exists() and final is not None,
        "history": state.get("history") or [], "cover": cover_paragraph(client_dir, filing, graph),
        "can_smooth": draft["smoothing_on"] and any(p["english_how"] in ("original", "machine") for p in draft["paragraphs"]),
        "needs_english": sum(1 for p in draft["paragraphs"] if p["language"] in LANGUAGES and p["language"] != "en" and p["english_how"] == "none"),
        "questions": [{"id": k, "label": label} for k, label, _s in questions(filing)],
        "languages": [[code, name] for code, name in LANGUAGES.items()],
    }


# -- the exhibit -------------------------------------------------------------------------------------------------------------


def _sheet():
    from review.bundle import _Sheet

    return _Sheet()


def _draft_mark(p) -> None:
    p.ops[:0] = ["q 0.88 g BT /F2 110 Tf 0.766 0.643 -0.643 0.766 120 190 Tm (DRAFT) Tj ET Q"]


def render(client_dir: Path, filing: str) -> Path | None:
    """declarations/declaration-<filing>.pdf from the final mark: the English, numbered; the client's own words in their
    language after it, with the same numbers; the 28 U.S.C. 1746 line and the signature. DRAFT on every page, with the
    reason at the foot, until the client's signature is recorded."""
    from pypdf import PdfWriter

    from fill.continuation import HEIGHT, MARGIN, WIDTH
    from review.bundle import _wrap

    client_dir = Path(client_dir)
    state = _state(client_dir, filing)
    final = state.get("final")
    if not final:
        return None
    signed = _signed(state)
    draft = declaration(client_dir, filing)
    name = draft["name"] or "[the client's name]"
    paragraphs = final["paragraphs"]
    width, size, lead = WIDTH - 2 * MARGIN, 11, 14.5
    pages: list = []
    y = 0.0

    def new_page():
        nonlocal y
        p = _sheet()
        if not signed:
            _draft_mark(p)
        pages.append(p)
        y = HEIGHT - 60
        return p

    p = new_page()

    def line(text: str, font: str = "F3", fsize: float = size, indent: float = 0, gap: float = 0) -> None:
        nonlocal p, y
        for part in _wrap(text, fsize, width - indent):
            if y < 70:
                p = new_page()
            p.text(MARGIN + indent, y, part, font, fsize)
            y -= lead if fsize >= size else fsize + 3
        y -= gap

    line(f"DECLARATION OF {name.upper()}", "F2", 15, gap=2)
    line(f"In support of {FILINGS[filing]['form']}", "F3", 10, gap=10)
    for n, para in enumerate(paragraphs, 1):
        line(f"{n}.  {para['english']}", gap=8)
    foreign = [(n, para) for n, para in enumerate(paragraphs, 1) if para["language"] != "en"]
    for lang in dict.fromkeys(para["language"] for _n, para in foreign):
        y -= 6
        line(f"The declarant's own words, in {LANGUAGES.get(lang, lang)}", "F2", 12, gap=4)
        for n, para in foreign:
            if para["language"] == lang:
                line(f"{n}.  {para['original']}", gap=8)
    y -= 10
    line(DECLARE_ABROAD if draft["abroad"] else DECLARE_IN_US, gap=24)
    if y < 140:
        p = new_page()
    p.line(MARGIN, y, MARGIN + 250, y)
    p.text(MARGIN, y - 12, f"Signature of {name}", "F3", 8)
    p.line(MARGIN + 300, y, WIDTH - MARGIN, y)
    if signed:
        p.text(MARGIN + 300, y + 4, signed["date"], "F3", 11)
    p.text(MARGIN + 300, y - 12, "Executed on (date, MM/DD/YYYY)", "F3", 8)
    machine = any(para["english_how"].startswith("machine") for para in paragraphs)
    for i, page in enumerate(pages):
        foot = (f"Signed by the declarant on {signed['date']} (recorded by {signed['who']})." if signed
                else "DRAFT: not for filing until the client signs." + (" The English is a machine translation until the firm's certified translator checks it." if machine else ""))
        page.text(MARGIN, 38, foot, "F2", 8)
        page.text(WIDTH - MARGIN - 56, 26, f"Page {i + 1} of {len(pages)}", "F3", 7)
    writer = PdfWriter()
    for page in pages:
        writer.add_page(page.to_page(writer))
    writer.add_metadata({"/Title": f"Declaration of {name}", "/Subject": f"In support of {FILINGS[filing]['form']}"})
    buf = io.BytesIO()
    writer.write(buf)
    out = pdf_path(client_dir, filing)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return out


def exhibit_entries(client_dir: Path, filing: str) -> list[dict[str, Any]]:
    """The declaration as a file of the packet's declaration exhibit (packet.plan): only once marked final, and only while
    the text still is what was marked. DRAFT until the client's signature is recorded."""
    if filing not in FILINGS or not (Path(client_dir) / STATE).exists():
        return []
    state = _state(client_dir, filing)
    if not state.get("final") or _final(client_dir, filing, state) is None:
        return []
    path = pdf_path(client_dir, filing)
    if not path.is_file():
        render(client_dir, filing)
    signed = _signed(state)
    return [{"doc": path.name, "type": "declaration", "path": str(path), "pages": None, "generated": True, "declaration": filing, "locked": True,
             "label": "The client's declaration" + (f", signed {signed['date']}" if signed else " (DRAFT until the client signs)")}]


def exhibit_for(schema: dict[str, Any], exhibits: dict[str, dict[str, Any]]) -> str | None:
    """The exhibit a declaration goes in: the one that takes the "declaration" type, else lists it as a role, else is named for it."""
    for test in (lambda ex: "declaration" in ex.get("types", []), lambda ex: "declaration" in ex.get("roles", []), lambda ex: ex["id"] == "declaration"):
        found = next((ex["id"] for ex in schema.get("exhibits", []) if test(ex)), None)
        if found in exhibits:
            return found
    return None


@producer(OFFICE)
def problems(client_dir: Path, filing: str) -> list[str]:
    """What stops the packet from being final, once the declaration is in it (or was, and changed)."""
    if filing not in FILINGS or not (Path(client_dir) / STATE).exists():
        return []
    state = _state(client_dir, filing)
    if not state.get("final"):
        return []
    draft = declaration(client_dir, filing)
    if _final(client_dir, filing, state, draft) is None:
        return [held(ATTORNEY, "The client's declaration changed after the attorney marked it final, so it is out of the packet: mark it final again "
                               "on the Declaration card.")]
    out = []
    if not _signed(state):
        out.append(held(CLIENT, "The client's declaration is not signed yet: once the client signs it, record the date on the Declaration card."))
    machine = sum(1 for p in state["final"]["paragraphs"] if p["english_how"].startswith("machine"))
    if machine:
        out.append(f"The client's declaration: the English of {machine} paragraph{'s are' if machine != 1 else ' is'} still the machine "
                   "translation. The firm's certified translator checks it and corrects it on the Declaration card.")
    return out


def checklist(client_dir: Path, filing: str) -> list[dict[str, str]]:
    if filing not in FILINGS or not (Path(client_dir) / STATE).exists():
        return []
    state = _state(client_dir, filing)
    signed = _signed(state) if state.get("final") and _final(client_dir, filing, state) else None
    return [{"kind": "sign", "text": f"The client signs the declaration in ink and writes the date (recorded as signed {signed['date']})."}] if signed else []


# -- the review bundle ---------------------------------------------------------------------------------------------------


def bundle_rows(client_dir: Path, filing: str | None) -> dict[str, Any] | None:
    """Where each paragraph of the declaration came from, for the review bundle (src/review/bundle.py): the question, the answer's
    source, date and language, how the English was made, every person's edit (old and new) and every smoothing (before and after),
    the final mark, the signature and the practice's approval. None when the filing has no declaration on file."""
    if filing not in FILINGS:
        return None
    draft = declaration(client_dir, filing)
    if not draft["paragraphs"]:
        return None
    state = _state(client_dir, filing)
    final = _final(client_dir, filing, state, draft)
    return {"paragraphs": draft["paragraphs"], "final": final and {k: v for k, v in final.items() if k != "paragraphs"},
            "signed": _signed(state) if final else None, "practice": practice(), "changed_since_final": bool(state.get("final")) and final is None}


# -- the cover letter's case paragraph ----------------------------------------------------------------------------------------

_BASES = [("asylum.basis_race", "race"), ("asylum.basis_religion", "religion"), ("asylum.basis_nationality", "nationality"),
          ("asylum.basis_political", "political opinion"), ("asylum.basis_social_group", "membership in a particular social group"),
          ("asylum.basis_torture", "the Convention Against Torture")]
# Form I-360, Part 2, Item 1: the boxes' own words (the I-360 template's tooltips, edition 01/20/25)
_VAWA_CLASS = {"Spouse": ("I", "Self-Petitioning Spouse of Abusive U.S. citizen or Lawful Permanent Resident"),
               "Child": ("J", "Self-Petitioning Child of Abusive U.S. citizen or Lawful Permanent Resident"),
               "Parent": ("K", "VAWA Self-Petitioning Parent of a U.S. citizen son or daughter")}


def _fact_source(graph, decisions: dict[str, dict[str, Any]], key: str) -> str:
    """Where one fact came from, in words: the person who answered it and when, the document, or the rule."""
    fact = graph.get(key)
    if fact is None:
        return ""
    review = getattr(fact, "review", None)
    if review is not None:
        decision = next((d for d in sorted(decisions.values(), key=lambda d: clock.key(d.get("at")), reverse=True)
                         if key in ((d.get("item") or {}).get("facts") or [])), None)
        when = clock.us_date((decision or {}).get("at"))
        return f"answered by {review.resolved_by}" + (f" on {when}" if when else "")
    if fact.derived_by:
        return f"worked out from the case ({fact.derived_by})"
    s = fact.sources[0] if fact.sources else None
    if s is None:
        return "the case"
    if s.doc_type in ("derived",) or str(s.doc_id).endswith(".derive"):
        return f"worked out from the case: {s.raw_value}"
    return f"read from {s.doc_id}"


def _fact_doc(graph, key: str) -> str | None:
    """The document a fact was read from, when that is its source (the screen names it in words: docName)."""
    fact = graph.get(key)
    if fact is None or getattr(fact, "review", None) is not None or fact.derived_by or not fact.sources:
        return None
    s = fact.sources[0]
    return None if s.doc_type == "derived" or str(s.doc_id).endswith(".derive") else s.doc_id


def cover_paragraph(client_dir: Path, filing: str, graph=None) -> dict[str, Any]:
    """The facts the cover letter states for this case, as sentences: [{"text", "facts": [{"key", "value", "source"}]}], and the
    paragraph ("text"). Fixed wording (DRAFT: docs/attorney_review.md) with values from the case's facts; a sentence whose fact is
    missing is left out. Only for the filings whose letter has the {case_paragraph} slot."""
    if filing not in FILINGS:
        return {"text": "", "sentences": []}
    from fill.cover_letter import long_date

    client_dir = Path(client_dir)
    graph = graph if graph is not None else _graph(client_dir, filing)
    decisions = _decisions(client_dir)
    who = "The " + FILINGS[filing]["who"]

    def v(key: str) -> Any:
        fact = graph.get(key)
        return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None

    def cite(*keys: str) -> list[dict[str, Any]]:
        return [{"key": k, "value": v(k), "source": _fact_source(graph, decisions, k), "doc": _fact_doc(graph, k)} for k in keys if v(k) is not None]

    def day(value: Any) -> str | None:
        m = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
        try:
            return long_date(date.fromisoformat(m.group(0))) if m else None
        except ValueError:
            return None

    out: list[dict[str, Any]] = []
    if filing == "i589":
        cat = v("asylum.cat") == "Yes"
        out.append({"text": f"{who} applies for asylum and for withholding of removal"
                            + (", and also for withholding of removal under the Convention Against Torture" if cat else "") + ".",
                    "facts": [{"key": "filing", "value": "Form I-589", "source": "the filing"}] + (cite("asylum.cat") if cat else [])})
        bases = [(k, words) for k, words in _BASES if v(k) == "Yes"]
        if bases:
            out.append({"text": "The application is based on " + _join([w for _k, w in bases]) + ", as marked in Part B, question 1 of Form I-589.",
                        "facts": cite(*[k for k, _w in bases])})
        arrival_key = next((k for k in ("applicant.last_arrival_date", "applicant.i94_arrival_date", "applicant.last_arrival_date_self_reported") if day(v(k))), None)
        if arrival_key:
            out.append({"text": f"{who} last arrived in the United States on {day(v(arrival_key))}.", "facts": cite(arrival_key)})
    elif filing == "u_visa":
        members = int(v("uvisa.members")) if str(v("uvisa.members") or "").isdigit() else 0
        out.append({"text": f"{who} petitions for U nonimmigrant status"
                            + (f", and for {members} qualifying family member{'s' if members != 1 else ''} on Form I-918, Supplement A" if members else "") + ".",
                    "facts": [{"key": "filing", "value": "Form I-918", "source": "the filing"}] + (cite("uvisa.members") if members else [])})
        crime = v("uvisa.crime_other") if v("uvisa.crime") and str(v("uvisa.crime")).startswith("A similar activity") else v("uvisa.crime")
        # "Attempt to commit any of the named crimes" (and conspiracy, solicitation) names no crime of its own: the sentence is left out
        if crime and not str(crime).startswith(("Attempt to commit", "Conspiracy to commit", "Solicitation to commit")):
            when, where = day(v("uvisa.crime_date")), v("uvisa.crime_place")
            out.append({"text": f"The qualifying criminal activity is {str(crime).lower() if crime == v('uvisa.crime') else crime}"
                                + (f", which took place on or about {when}" if when else "") + (f" in {where}" if where else "") + ".",
                        "facts": cite("uvisa.crime", "uvisa.crime_other" if crime != v("uvisa.crime") else "uvisa.crime",
                                      *(["uvisa.crime_date"] if when else []), *(["uvisa.crime_place"] if where else []))})
            out[-1]["facts"] = list({f["key"]: f for f in out[-1]["facts"]}.values())
        if day(v("uvisa.supb_signed")):
            out.append({"text": f"The certifying official signed the enclosed Form I-918, Supplement B, on {day(v('uvisa.supb_signed'))}.",
                        "facts": cite("uvisa.supb_signed")})
    elif filing == "i914":
        count = int(v("tvisa.family_count")) if str(v("tvisa.family_count") or "").isdigit() else 0
        out.append({"text": f"{who} applies for T nonimmigrant status"
                            + (f", and for {count} family member{'s' if count != 1 else ''} on Form I-914, Supplement A" if count else "") + ".",
                    "facts": [{"key": "filing", "value": "Form I-914", "source": "the filing"}] + (cite("tvisa.family_count") if count else [])})
        if v("tvisa.victim") == "Yes":
            out.append({"text": f"In Part 3, Item 1 of Form I-914, the {FILINGS[filing]['who']} answers Yes: a victim of a severe form of trafficking in persons.",
                        "facts": cite("tvisa.victim")})
        if day(v("tvisa.trafficking_began")):
            out.append({"text": f"The trafficking began on or about {day(v('tvisa.trafficking_began'))}.", "facts": cite("tvisa.trafficking_began")})
    elif filing == "vawa":
        import vawa

        if not vawa.filed_already(graph) and v("vawa.classification") in _VAWA_CLASS:
            letter, words = _VAWA_CLASS[v("vawa.classification")]
            both = vawa.concurrent(graph)
            out.append({"text": f"{who} requests classification as a {words} (Form I-360, Part 2, Item 1.{letter})"
                                + (", and files Form I-485 in the same package" if both else "") + ".",
                        "facts": cite("vawa.classification", *(["vawa.concurrent_i485"] if both else []))})
            if v("vawa.abuser_status") and v("vawa.abuser_status") != vawa.OTHER:  # "Other" is explained in words: not a sentence here
                out.append({"text": f"The abuser is now, or was, a {v('vawa.abuser_status')} (Form I-360, Part 10, Item 5).", "facts": cite("vawa.abuser_status")})
            if v("vawa.classification") == "Spouse" and day(v("vawa.marriage_date")):
                place = v("vawa.marriage_place")
                out.append({"text": f"{who} married the abuser on {day(v('vawa.marriage_date'))}" + (f" in {place}" if place else "") + ".",
                            "facts": cite("vawa.marriage_date", *(["vawa.marriage_place"] if place else []))})
    return {"text": " ".join(s["text"] for s in out), "sentences": out}


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
