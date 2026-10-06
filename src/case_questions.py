"""Ask about this case: a question in plain words, answered with the case's own record, every line followed by where it came from
(docs/design_plan.md, wave H, H4). And the summary for the attorney, built from the record the same way.

THE BOUND (docs/ARCHITECTURE.md): a model reads, it never decides a legal question. Here it does less than read aloud: it only
CHOOSES which of the record's passages answer a question, by their numbers. Nothing the model writes is ever shown, logged as the
answer, or printed: the answer is the chosen passages, each in the record's own words with its citation (extractive). The verifier's
two rounds on the earlier design (the model wrote sentences and the product checked them) found false sentences a word check let
through each time; a passage the record holds cannot say anything the record does not.

How an answer is made:
  1. The record, as passages (passages()): one fact a passage, so a line on screen is one fact about one subject. The case's facts
     with their sources ("Date of last arrival (I-94): 07/15/2019", cited "Date of last arrival (I-94) from the I-94 arrival record,
     page 1"; a list of people is a passage per person), its documents (that the case holds each, each date, its language, what was
     assumed, its text a line at a time, cited by name and page), its review decisions, the track and stage, the timeline, the open
     steps and deadlines (a deadline and its due date apart), each notice's dates apart, the mailing records, the hearings. Only this
     case's folder is read: no other case's record reaches the model.
  2. Retrieval (retrieve()): the passages that share words with the question (its words and plain synonyms; a relative named in the
     question brings that person's name from the facts). Nothing found: REFUSAL, and the model is not asked.
  3. Only a record lookup goes to the model (record_lookup()): a question shaped like a lookup (what, when, where, which, who, how
     many, is there, did she, does the case have; staff's "do we have", "did she respond", "what did the attorney decide") about
     something the record holds (a document, a fact, a decision, a step, a notice, a filing, a date, a person on the case), with
     nothing in it asking for a judgment (VETO). Every other question is the attorney's call: ATTORNEYS_CALL and the passages found.
  4. A lookup: the numbered passages go to the local model after the practice text (PRACTICE, approved by the attorney like a rule,
     src/rules/approval.py; it is the instruction the model receives, word for word) and the question. The model answers with
     passage numbers, or NOT IN THE RECORD (REFUSAL). The chosen passages are shown in the record's order, each once, at most
     MAX_SENTENCES, dates as MM/DD/YYYY. Numbers it was never given are ignored; when it chooses nothing usable, UNCHOSEN and the
     passages found. A document's own text that reads like advice is never among the lines.

Logged: every question and summary, the passages used, the model's own text and the numbers it chose, the lines shown and who asked go
to the case's questions.jsonl (the question is the person's words: it stays under the case, behind the same gate as every route that
opens the case), and one row goes to the event ledger (src/events.py) saying who asked and how it ended, without the question's words.

Off until an attorney switches it on in Settings, Drafting and models (is_on), and not used until the practice is approved.
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import clock
import events
import schema_path

LOG = "questions.jsonl"
PRACTICE_ID = "PRACTICE:CASE-QUESTIONS"
PRACTICE_NAME = "Questions about a case answered by the local model"
# The practice the attorney approves (Settings, Drafting and models, or Keeping current): the instruction the model receives, word for
# word, before the numbered passages and the question. A change to these words needs the attorney's approval again (its hash).
PRACTICE = ("You help a member of the law firm's staff find what one client's case record says. Below are numbered passages, each taken "
            "word for word from this case's own record, and a question. Choose the passages that answer the question. Answer with their "
            "numbers only, separated by commas, like: 2, 5. Write no words and no sentences. If no passage answers the question, answer "
            "only: NOT IN THE RECORD. The firm shows staff the passages you choose exactly as the record writes them, each with where it "
            "came from, and never shows anything you write. Only choose passages: whether anyone is eligible, what anyone should do and "
            "what is likely to happen are the attorney's to decide.")
PRACTICE_SOURCE = ("The firm's own practice for questions about a case, written for the attorney's approval: no statute or rule requires it. "
                   "These words are what the local model is told, before the passages and the question")
NOT_IN_RECORD = "NOT IN THE RECORD"  # the model's way of saying no passage answers (the practice text says it)

# What staff read (DRAFT staff wording, docs/attorney_review.md)
REFUSAL = "The case's record does not say."
ATTORNEYS_CALL = "That is the attorney's call. Here is what the record holds:"
ATTORNEYS_CALL_ALONE = "That is the attorney's call."
UNCHOSEN = "The local model did not choose from the record; here is what the record holds on it:"
EMPTY = "The local model answered nothing; here is what the record holds on it:"
NEUTRAL = "Here is what the record holds on it:"
MORE_LINES = "And {n} more lines of {name}."
# A question asked in Portuguese or Spanish with no word the product can read: said in that language (DRAFT, like all such wording)
ENGLISH_ONLY = {"pt": "Por enquanto, as perguntas sobre o caso são feitas em inglês. Pergunte em inglês, por favor.",
                "es": "Por ahora, las preguntas sobre el caso se hacen en inglés. Pregunte en inglés, por favor."}
UNREACHABLE = "The local model could not be reached, so nothing was answered. Try again later; the case's record is unchanged."
CUT_OFF = "The local model's answer ran too long and was cut off, so nothing was answered. Ask a narrower question."
SHORTENED = "The answer was shortened: only its first lines are shown."
NONE_RECORDED = "None recorded."
MORE = "{n} more in the record were left out of this part; the case page lists them all."
OFF = "Questions about a case are off. An attorney can switch them on in Settings (Drafting and models)."
NOT_APPROVED = ("The practice for questions about a case is not approved yet. An attorney reads it and approves it in Settings "
                "(Drafting and models) before anyone can ask.")
CHANGED = ("The practice for questions about a case changed since the attorney approved it. An attorney approves it again in Settings "
           "(Drafting and models) before anyone can ask.")
INTRO = ("Ask in plain words, for example: when did she enter, which documents mention her father, what did the attorney decide about "
         "her name, is there an I-94. The answer is the record's own lines, chosen for you, each with where it came from. A question that "
         "asks for a judgment is the attorney's call: you see what the record holds on it.")
SUMMARY_NOTE = ("DRAFT summary for the attorney, built from this case's own record: each line is the record's own words, with where it came "
                "from. It is never part of a filing, a packet or the review bundle.")
QUESTION_MAX = 500  # characters in one question
TOP = 10  # passages handed to the model for one question
SUMMARY_TOP = 24  # lines in one part of the summary
NUM_PREDICT = 60  # tokens of answer: a few passage numbers
QUESTION_TIMEOUT = 60  # seconds the local model has for one question (never sent again after a timeout)
MAX_SENTENCES = 12  # lines shown in one answer
TEXT_PER_DOCUMENT = 3  # lines of one document's own text in one answer
RAW_MAX = 20000  # characters of the model's own text the log keeps


# -- the switch and the practice --------------------------------------------------------------------------------------------


def is_on() -> bool:
    """The attorney's switch (Settings, Drafting and models), off unless switched on."""
    import settings

    return settings.values("drafting").get("case_questions") == "on"


def practice() -> dict[str, Any]:
    """The attorney's approval of this practice (src/rules/approval.py): {id, name, state, text (the approval in words), plain_text}."""
    from review.state import rule_info

    info = rule_info(PRACTICE_ID)
    return {"id": PRACTICE_ID, "name": PRACTICE_NAME, "state": info["approval"]["state"], "text": info["approval_text"], "plain_text": info["plain_text"]}


def why_not() -> str | None:
    """Why nobody can ask right now, in words, or None."""
    if not is_on():
        return OFF
    state = practice()["state"]
    return None if state == "approved" else CHANGED if state == "changed" else NOT_APPROVED


# -- words --------------------------------------------------------------------------------------------------------------------


def _fold(text: Any) -> str:
    """Lower case, accents off ("José" and "JOSE" are the same name)."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(c)).casefold()


_WORD = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")
STOP = frozenset("""a an the and or but if of to in on at by for from with about as into onto over under than then there here this that these
those is are was were be been being am do does did done has have had having it its it's he she they them their his her hers him we our us
i me my mine your yours you what which who whom whose when where why how whether any some all each every no not nor so such can could
would will shall may might must should also just only very more most much many one ones case client clients record records please tell
show give list find say says said know""".split())
# Plain synonyms for retrieval: a word of the question and the words the record uses for the same thing
SYNONYMS = [
    {"enter", "entered", "entry", "entering", "arrive", "arrived", "arrival", "arriving", "came", "come", "admitted", "admission", "admit"},
    {"born", "birth", "dob", "birthday"},
    {"father", "dad", "pai", "padre", "parent", "parents"},
    {"mother", "mom", "mae", "madre", "parent", "parents"},
    {"name", "names", "named", "called"},
    {"decide", "decided", "decision", "decisions", "confirmed", "corrected", "acknowledged", "approved", "reviewer"},
    {"mail", "mailed", "sent", "send", "filed", "file", "filing", "submitted", "mailing"},
    {"notice", "notices", "receipt", "receipts", "approval", "letter"},
    {"passport", "passaporte", "travel"},
    {"i94", "94", "arrival"},
    {"deadline", "deadlines", "due", "until"},
    {"stage", "track", "status", "stands"},
    {"live", "lives", "lived", "living", "address", "addresses", "home", "reside", "resided"},
    {"work", "worked", "job", "jobs", "employer", "employed", "occupation", "school"},
    {"married", "marriage", "spouse", "husband", "wife", "marital"},
    {"child", "children", "kids", "son", "daughter"},
    {"country", "citizenship", "citizen", "nationality", "national"},
    {"hearing", "court", "judge"},
    {"expire", "expires", "expired", "expiry", "expiration", "valid"},
    {"anumber", "a-number"},
]
_SYN: dict[str, set[str]] = {}
for _group in SYNONYMS:
    for _w in _group:
        _SYN.setdefault(_w, set()).update(_group)
# A relative named in a question, and the fact keys that hold that person's name (their name then finds the documents that mention them)
RELATIVES = {"father": ("applicant.father_given_name", "applicant.father_family_name", "questionnaire.father_name"),
             "dad": ("applicant.father_given_name", "applicant.father_family_name", "questionnaire.father_name"),
             "mother": ("applicant.mother_given_name", "applicant.mother_family_name", "questionnaire.mother_name"),
             "mom": ("applicant.mother_given_name", "applicant.mother_family_name", "questionnaire.mother_name"),
             "spouse": ("spouse.given_name", "spouse.family_name"), "husband": ("spouse.given_name", "spouse.family_name"),
             "wife": ("spouse.given_name", "spouse.family_name"), "petitioner": ("petitioner.given_name", "petitioner.family_name")}


def _stem(word: str) -> str:
    """A light stem so "arrival" and "arrived" meet: the first six letters of a long word."""
    for end in ("ing", "ed", "es", "s"):
        if len(word) > len(end) + 3 and word.endswith(end):
            word = word[: -len(end)]
            break
    return word[:6]


def _words(text: Any) -> list[str]:
    return _WORD.findall(_fold(text).replace("i-94", "i94").replace("a-number", "anumber"))


def _stems(text: Any) -> set[str]:
    return {_stem(w) for w in _words(text)}


# -- dates and values ------------------------------------------------------------------------------------------------------------


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_MON = "|".join(m[:3] + r"[a-z]*" for m in MONTHS) + "|fev|abr|mai|ago|set|out|dez"  # and the Portuguese months a passport prints
_PT_MONTHS = {"fev": 2, "abr": 4, "mai": 5, "ago": 8, "set": 9, "out": 10, "dez": 12}
_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DATES = [  # (pattern, (year, month, day) from the match): the slash form is read apart (which part is the day depends on the document)
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), lambda m: (m[1], m[2], m[3])),
    (re.compile(r"\b(\d{4})\s+(" + _MON + r")\.?\s+(\d{1,2})\b", re.I), lambda m: (m[1], _month(m[2]), m[3])),
    (re.compile(r"\b(" + _MON + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.I), lambda m: (m[3], _month(m[1]), m[2])),
    (re.compile(r"\b(\d{1,2})\s+(" + _MON + r")(?:/[A-Za-z]{3})?\.?,?\s+(\d{4})\b", re.I), lambda m: (m[3], _month(m[2]), m[1])),  # "10 JAN/JAN 2032"
]
AMBIGUOUS = "(day and month as the document writes them)"  # after a date read from a foreign document whose day could be its month


def _month(word: str) -> str:
    w = word.lower()[:3]
    return f"{_PT_MONTHS.get(w) or next(i for i, m in enumerate(MONTHS, 1) if m.startswith(w)):02d}"


def _iso(y: Any, mo: Any, d: Any) -> str | None:
    try:
        from datetime import date as _date

        return _date(int(y), int(mo), int(d)).isoformat()
    except (TypeError, ValueError):
        return None


def find_dates(text: Any, day_first: bool = False) -> list[tuple[int, int, list[str], bool]]:
    """Every date in the text: (start, end, its readings as YYYY-MM-DD, ambiguous). A slash date reads month first (MM/DD/YYYY), or day
    first in a document from a country that writes it so (day_first); a part over 12 decides it either way. "Ambiguous": both parts 12
    or under, so either reading is a date (both readings are given, the document's own first)."""
    text = str(text or "")
    out: list[tuple[int, int, list[str], bool]] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    for pattern, parts in _DATES:
        for m in pattern.finditer(text):
            iso = _iso(*parts(m))
            if iso and free(m.start(), m.end()):
                out.append((m.start(), m.end(), [iso], False))
                taken.append((m.start(), m.end()))
    for m in _SLASH.finditer(text):
        if not free(m.start(), m.end()):
            continue
        a, b, y = int(m[1]), int(m[2]), m[3]
        md, dm = _iso(y, a, b), _iso(y, b, a)
        readings = [r for r in ((dm, md) if day_first else (md, dm)) if r]
        readings = list(dict.fromkeys(readings))
        if readings:
            out.append((m.start(), m.end(), readings, len(readings) > 1))
            taken.append((m.start(), m.end()))
    return sorted(out)


def dates_in(text: Any, day_first: bool = False) -> tuple[set[str], str]:
    """(every date in the text as YYYY-MM-DD, by its first reading; the text with the dates taken out)."""
    text = str(text or "")
    found, rest, at = set(), [], 0
    for s, e, readings, _amb in find_dates(text, day_first):
        found.add(readings[0])
        rest.append(text[at:s] + " ")
        at = e
    return found, "".join(rest) + text[at:]


def us(value: Any) -> str:
    """A value as the screen writes it: an ISO date (alone or inside the text) as MM/DD/YYYY."""
    text = str(value if value is not None else "")
    return re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: f"{m[2]}/{m[3]}/{m[1]}", text)


_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _hide(text: str) -> str:
    """A Social Security number in a document's text shows its last four digits only, as everywhere else."""
    return _SSN.sub(lambda m: "***-**-" + m.group(0)[-4:], text)


def _client_words(text: str) -> str:
    """A form's question about the applicant, as the record's words about the client: "Have you EVER been denied a visa" reads
    "Has the client ever been denied a visa" (an answer never addresses the client, so its passages don't either)."""
    out = re.sub(r"\bEVER\b", "ever", text)
    out = re.sub(r"\b[Hh]ave you\b", "Has the client", out)
    out = re.sub(r"\b[Aa]re you\b", "Is the client", out)
    out = re.sub(r"\b[Dd]o you\b", "Does the client", out)
    out = re.sub(r"\b[Ww]ere you\b", "Was the client", out)
    out = re.sub(r"\b[Dd]id you\b", "Did the client", out)
    out = re.sub(r"\byours\b", "the client's", out)
    out = re.sub(r"\b[Yy]our\b", "the client's", out)
    out = re.sub(r"\b[Yy]ou\b", "the client", out)
    # the form's first person ("When I last arrived", "I am filing this Form I-485 as a"): about the client too
    out = re.sub(r"\bI am\b", "the client is", out)
    out = re.sub(r"\bI was\b", "the client was", out)
    out = re.sub(r"\b[Mm]y\b", "the client's", out)
    out = re.sub(r"\bI\b(?!-)", "the client", out)
    out = re.sub(r"\bthe client were\b", "the client was", out)
    return out


# -- the record, as passages ------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _catalog():
    """The I-485's labels for fact keys (the review screen's), built once."""
    from review.server import load_field_map
    from review.state import Catalog

    repo = Path(__file__).resolve().parents[1]
    return Catalog(load_field_map(schema_path.path("field_map", "i485", schema_path.schemas_in(repo))), schema_path.path("template", "i485", schema_path.schemas_in(repo)), [])


# Labels the record's plain words give the facts staff ask about most (the form's own wording elsewhere)
LABELS = {"applicant.i94_arrival_date": "Date of last arrival (I-94)", "applicant.last_arrival_date_self_reported": "Date of last entry as the client wrote it",
          "applicant.i94_admit_until_date": "Admitted until (I-94)", "applicant.i94_class_of_admission": "Class of admission (I-94)",
          "applicant.i94_number": "I-94 number", "applicant.i94_given_name": "Given name on the I-94", "applicant.i94_family_name": "Family name on the I-94",
          "applicant.last_arrival_city": "City of last arrival", "applicant.last_arrival_state": "State of last arrival",
          "applicant.last_arrival_manner": "How the client last arrived", "applicant.a_number": "A-Number", "questionnaire.a_number": "A-Number the client wrote",
          "applicant.dob": "Date of birth", "applicant.given_name": "Given name", "applicant.family_name": "Family name",
          "applicant.citizenship": "Country of citizenship", "applicant.country_of_birth": "Country of birth", "applicant.birth_city": "City of birth",
          "applicant.sex": "Sex", "applicant.marital_status": "Marital status", "applicant.ssn": "Social Security number",
          "applicant.i360_receipt_number": "I-360 receipt number", "applicant.i360_priority_date": "I-360 priority date",
          "applicant.travel_document_number": "Passport number used at the last arrival", "applicant.travel_document_expiry": "Passport expiry date",
          "applicant.travel_document_country": "Country that issued the passport", "applicant.current_status_text": "Current immigration status",
          "applicant.filing_category": "Filing category", "applicant.has_a_number": "Has an A-Number", "applicant.birth_certificate_name": "Name on the birth certificate",
          "applicant.birth_cert.parent_a_name": "First parent named on the birth certificate", "applicant.birth_cert.parent_b_name": "Second parent named on the birth certificate",
          "applicant.birth_cert.grandparents": "Grandparents named on the birth certificate", "questionnaire.entry_how": "How the client entered",
          "applicant.birth_cert.naturalidade": "Place of birth written on the birth certificate",
          "applicant.birth_cert.parent_a_birthplace": "Place of birth of the first parent named on the birth certificate",
          "applicant.birth_cert.parent_b_birthplace": "Place of birth of the second parent named on the birth certificate",
          "applicant.last_arrival_admitted_as": "When the client last arrived: admitted as (class of admission)",
          "applicant.filing_as": "The client files this Form I-485 as",
          "applicant.name_current": "The client's current legal name (the name on every form)",
          "applicant.name_uscis_ok": "The name this filing uses, where USCIS knows the client by another (the attorney's decision)",
          "applicant.name_current_typed": "The client's current legal name as the client wrote it",
          "applicant.name_birth_typed": "The client's name at birth as the client wrote it",
          "applicant.name_chosen_given": "The client's current given name as a reviewer typed it on the names card",
          "applicant.name_chosen_family": "The client's current family name as a reviewer typed it on the names card",
          "questionnaire.father_name": "Father's full name as the client wrote it", "questionnaire.mother_name": "Mother's full name as the client wrote it",
          "questionnaire.visa_type": "Visa type the client wrote", "questionnaire.has_i94_or_parole": "The client says they have an I-94 or parole"}
_GENERIC = {"street", "city", "state", "zip code", "country", "from", "to", "given name", "family name", "province", "postal code",
            "apt / ste / flr number", "occupation", "employer or school"}
# A generic label ("Street") takes the words of what it belongs to, from the key ("physical_street": the home address)
OWNERS = [("physical", "Home address"), ("mailing", "Mailing address"), ("employer1", "Current job or school"), ("prior_address", "Previous address"),
          ("last_foreign", "Last address outside the U.S."), ("foreign_employer", "Last job outside the U.S."), ("last_arrival", "Last arrival"),
          ("organization", "Organization"), ("i94", "I-94"), ("travel_document", "Passport"), ("spouse", "Spouse"), ("mother", "Mother"),
          ("father", "Father"), ("birth", "Birth")]
_QUESTION_START = re.compile(r"\b(?:Have|Has|Are|Is|Do|Does|Did|Was|Were|What|When|Where|Which|Provide|If|I am|Indicate)\b")
_SKIP = ("firm.", "folder.", "applicant.p14_", "applicant.na.", "questionnaire.blank.", "intake.", "office.",
         "applicant.name_events",  # the name timeline's own record (src/name_events.py): its names are the facts it settled
         "applicant.name_after_marriage_open",  # the timeline's marker while the names card asks (brief K6): not a fact about the client
         "applicant.name_change_said",  # the same while the client says the name changed: the client's answer is questionnaire.name_changed
         "applicant.name_choice_documents")  # the documents on the names card when a person chose: a record, not a fact about the client


def fact_label(key: str) -> str:
    """A fact in plain words, never its key: the record's own label where there is one; else the review screen's short label, a
    generic one ("Street") with what it belongs to ("Home address: street"), a form question from its first word ("Have you ever
    been denied a visa"); a form question in the second person reads as one about the client."""
    from review.state import concise, short_label

    if key in LABELS:
        return LABELS[key]
    org = re.fullmatch(r"(?:questionnaire\.organization|applicant\.part9\.org)(\d+)_(\w+)", key)
    if org:  # "Org1 city", "Organization1 involvement": in words
        part = {"name": "name", "city": "city", "state": "state", "country": "country", "nature": "what it is", "involvement": "the client's part in it",
                "date_from": "from", "date_to": "to"}.get(org.group(2), events.words(org.group(2)))
        return f"Organization {org.group(1)} the client belongs or belonged to: {part}"
    full = _catalog().label(key)
    label = short_label(key, full)
    if label.lower() in _GENERIC:
        last = key.rsplit(".", 1)[-1]
        owner = next((words for start, words in OWNERS if last.startswith(start)), None)
        label = f"{owner}: {label.lower()}" if owner else events.words(last).capitalize()
    elif re.match(r"(Part \d|\d|To be completed)", label):
        m = _QUESTION_START.search(label)
        label = label[m.start():] if m else concise(full, 90)
    label = _client_words(re.sub(r"^Enter\s+", "", concise(label, 120)))
    return (label[:1].upper() + label[1:]).rstrip(" :")


def _a_name(type_name: str) -> str:
    """"Birth certificate" -> "the birth certificate"; "I-94 arrival record" keeps its capitals; "The client's questionnaire" stays."""
    if type_name.lower().startswith("the "):
        return "the " + type_name[4:]
    proper = re.match(r"[A-Z]-?\d|[A-Z]{2}|[A-Z][a-z]+ [A-Z]", type_name)  # "I-94 ...", "USCIS ...", "Social Security card"
    return "the " + (type_name if proper else type_name[:1].lower() + type_name[1:])


def _source_words(s, record_of: Callable[[str], dict | None], page_of: Callable[[dict, Any], int | None]) -> str:
    """Where a fact's source is, in words, as the review bundle says it: a document's name and page; the client's answer in the
    portal with its date; the firm's details. Never a file name."""
    from portal.questions import OFFICE_DOC_ID

    doc, kind = str(s.doc_id or ""), s.doc_type or ""
    when = clock.us_date(s.extracted_at)
    if kind == "firm_profile":
        return "the firm's details"
    if kind == "office_question" or doc == OFFICE_DOC_ID:
        return "the client's answer to the office's question" + (f" ({when})" if when else "")
    if doc in ("portal questionnaire", "portal") or (kind in ("portal", "intake_questionnaire") and not doc.lower().endswith(".pdf")):
        return "the client's answer in the portal" + (f" ({when})" if when else "")
    import documents

    record = record_of(doc)
    page = page_of(record, s.raw_value) if record else None
    return _a_name(documents.name(kind or (record or {}).get("type") or "unclassified")) + (f", page {page}" if page else "")


class Record:
    """One case's record, read once for a question: its folder only."""

    def __init__(self, client_dir: Path):
        self.dir = Path(client_dir)
        import documents
        from review.state import load_decisions, reviewed_graph

        self.graph = reviewed_graph(self.dir)
        self.docs = documents.load(self.dir)["documents"]
        self.by_doc = {d: r for r in self.docs for d in (r.get("doc_ids") or r.get("files") or [])}
        self.decisions = load_decisions(self.dir)
        meta = self.dir / "meta.json"
        self.folder = Path(json.loads(meta.read_text(encoding="utf-8")).get("source_folder") or self.dir) if meta.exists() else self.dir
        self._pages: dict[str, list[str] | None] = {}

    def page_texts(self, record: dict) -> list[str] | None:
        """Each page's text of a document of more than one page (from the file's own text layer), or None."""
        doc = (record.get("doc_ids") or record.get("files") or [""])[0]
        if doc in self._pages:
            return self._pages[doc]
        out = None
        file = self.folder / doc.partition("#")[0]
        if len(record.get("pages") or []) > 1 and file.suffix.lower() == ".pdf" and file.is_file():
            try:
                from pypdf import PdfReader

                reader = PdfReader(str(file))
                out = [reader.pages[n - 1].extract_text() or "" if 1 <= n <= len(reader.pages) else "" for n in record["pages"]]
            except Exception:  # noqa: BLE001 -- a file that can't be read: the page is not said, the passage stays
                out = None
        self._pages[doc] = out
        return out

    def page_of(self, record: dict | None, needle: Any) -> int | None:
        """The page of the file a value or a line stands on: the document's only page, else the page whose text holds it."""
        if not record:
            return None
        pages = record.get("pages") or []
        if len(pages) <= 1:
            return pages[0] if pages else 1
        texts = self.page_texts(record)
        want = _fold(needle).split("\n")[0].strip()[:40]
        if texts and want:
            for n, text in zip(pages, texts):
                if want in _fold(text):
                    return n
        return None


def _passage(ref: str, kind: str, says: str, cite: str, topics: set[str], search: str = "", **extra) -> dict[str, Any]:
    return {"ref": ref, "kind": kind, "says": " ".join(str(says).split()), "cite": cite, "topics": sorted(topics),
            "search": " ".join([says, cite, search])} | extra


WHO_KEYS = ("applicant.given_name", "applicant.family_name", "applicant.dob", "applicant.sex", "applicant.country_of_birth", "applicant.birth_city",
            "applicant.citizenship", "applicant.a_number", "applicant.marital_status", "applicant.current_status_text", "applicant.filing_category")


def _fact_passages(rec: Record) -> list[dict[str, Any]]:
    """A passage per fact; a fact that lists several people or things ("Grandparents named on the birth certificate: PEDRO EXEMPLO;
    LUCIA EXEMPLO") is a passage per person, so a line on screen is one fact about one subject."""
    from review.state import is_private_number, mask_number, rule_info, rule_name

    out = []
    for key, fact in sorted(rec.graph.all_facts().items()):
        if key.startswith(_SKIP) or fact.status not in ("resolved", "conflict") or fact.value in (None, "", [], {}):
            continue
        label = fact_label(key)
        private = is_private_number(key, label)

        def shown(v: Any) -> str:
            text = us(v if not isinstance(v, (list, tuple)) else ", ".join(map(str, v)))
            return mask_number(text) if private else text

        given = [s for s in fact.sources if s.doc_type not in ("derived", "paralegal_review") and s.doc_id not in ("fact_graph", "paralegal_review")]
        sources = [_source_words(s, rec.by_doc.get, rec.page_of) for s in given]
        wheres = list(dict.fromkeys(sources))
        if fact.review is not None:
            cite = f"{label}, set by {fact.review.resolved_by}" + (f" on {clock.us_date(fact.review.resolved_at)}" if clock.us_date(fact.review.resolved_at) else "")
        elif fact.derived_by:  # a rule by its name in words; a firm policy by its plain name, never its code
            how = (f"the firm's standard answer \"{rule_info(fact.derived_by)['name']}\"" if fact.derived_by.startswith("POLICY:")
                   else rule_name(fact.derived_by))
            cite = f"{label}, worked out by {how}" + (f" from {wheres[0]}" if wheres else "")
        elif wheres:
            cite = f"{label} from " + (" and ".join(wheres[:2]))
        else:
            cite = label
        topics = {"who"} if key in WHO_KEYS else set()
        if fact.status == "conflict":  # the record's own open question: one line, every side's value beside its source
            sides = [f"{w} says {shown(s.normalized_value)}" for w, s in zip(sources, given)]
            if fact.derived_by:  # a rule's or policy's own value is a side too ("the overstay rule ... says Yes")
                rule = (f"the firm's standard answer \"{rule_info(fact.derived_by)['name']}\"" if fact.derived_by.startswith("POLICY:")
                        else rule_name(fact.derived_by))
                sides.append(f"{rule} says {shown(fact.value)}")
            told = "; ".join(dict.fromkeys(sides))
            out.append(_passage(f"fact:{key}", "fact", f"{label}: the sources disagree ({told}).", cite, topics | {"open"}, events.words(key)))
            continue
        listed = isinstance(fact.value, str) and re.search(r"(grandparents|parents|children|names)$", key)  # a list of people
        values = [v.strip() for v in str(shown(fact.value)).split(";") if v.strip()] if listed else [shown(fact.value)]
        for n, value in enumerate(values, 1):
            ref = f"fact:{key}" if len(values) == 1 else f"fact:{key}:{n}"
            out.append(_passage(ref, "fact", f"{label}: {value}", cite, topics, events.words(key),
                                yes_no=str(fact.value) if str(fact.value) in ("Yes", "No") else None))
    return out


PERSON_WORDS = {"applicant": "the client's", "unknown": "whose is not known", "spouse": "the spouse's", "petitioner": "the petitioner's",
                "parent": "a parent's", "child_1": "the first child's", "child_2": "the second child's", "child_3": "the third child's"}
LANG = {"en": "English", "pt": "Portuguese", "es": "Spanish", "fr": "French", "ht": "Haitian Creole", "unknown": "not known"}
QUALITY = {"readable": "readable", "blurry": "blurry", "cut_off": "cut off", "partial": "partial", "check": "to check by eye", "unknown": "not measured"}
EXPIRES = {"expires": "expires", "admit_until": "admitted until", "valid_to": "valid to"}
ISSUED = {"issued": "issued", "registered": "registered", "arrived": "arrival", "notice": "notice date", "order": "order date"}
TEXT_LINES = 60  # lines of one document's text that become passages


_CONTINUES = re.compile(r"\b(?:e|y|and|de|da|do|dos|das|del)\s+[A-ZÀ-Ý][A-ZÀ-Ý'-]*$")  # "..., e MARIA" / "EXEMPLO LIMA, ...": a name broken


def _joined(lines: list[str]) -> list[str]:
    """A document's lines, with what belongs together on one line: a name broken across two lines ("... e MARIA" / "EXEMPLO LIMA, ...")
    joined; a short heading ("NOME: /", "FILIAGAO", "AVOS") joined to the line it heads; a table's header line (words, no colon:
    "Received Date Priority Date Petitioner A099 000 123") joined to the line of its values below it (no small letters:
    "02/10/2025 02/10/2025 EXEMPLO SOUZA, ANA CLARA"), so a value is never shown without its label."""
    out: list[str] = []
    for line in lines:
        if out and _CONTINUES.search(out[-1]):
            out[-1] = f"{out[-1]} {line}"
            continue
        prev = out[-1] if out else ""
        heading = prev and len(prev.split()) <= 2 and not re.search(r"\d", prev) and (prev.endswith((":", "/")) or prev.isupper())
        values_only = not re.search(r"[a-z]{2,}", re.sub(r"\b(?:of|e|y)\b", "", line))  # no word in small letters ("1 of 1" is a value)
        header = bool(prev) and " / " not in prev and ":" not in prev and bool(re.search(r"[a-z]{2,}", prev)) and values_only and len(prev.split()) >= 2
        if heading:
            out[-1] = f"{prev.rstrip(' /:')} / {line}"
        elif header:
            out[-1] = _paired(prev, line)
        else:
            out.append(line)
    return out


def _paired(header: str, values: str) -> str:
    """A table's header and values on one line, each date beside its own label when the header names the dates in the order the values
    give them ("Received Date Priority Date Petitioner ..." over "02/10/2025 02/10/2025 EXEMPLO SOUZA, ANA CLARA": "Received Date
    02/10/2025; Priority Date 02/10/2025; Petitioner ... / EXEMPLO SOUZA, ANA CLARA"); otherwise the two lines side by side."""
    labels = re.findall(r"(?:[A-Z][a-z]+ )?Date\b", header)
    tokens = values.split()
    n = len(labels)
    if labels and len(tokens) >= n and all(re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", t) for t in tokens[:n]):
        rest_header = " ".join(re.sub(r"(?:[A-Z][a-z]+ )?Date\b", " ", header).split())
        rest_values = " ".join(tokens[n:])
        pairs = "; ".join(f"{label} {day}" for label, day in zip(labels, tokens[:n]))
        return pairs + (f"; {rest_header}" if rest_header else "") + (f" / {rest_values}" if rest_values else "")
    return f"{header} / {values}"


def _document_passages(rec: Record) -> list[dict[str, Any]]:
    """A document's record as one passage per fact (that the case holds it, whose; each date; its language; what was assumed; when
    it was added), and its text a line at a time, each cited by the document's name and page."""
    import documents

    out = []
    for r in rec.docs:
        tname = documents.name(r.get("type") or "unclassified")
        title = tname[:1].upper() + tname[1:]
        whose = PERSON_WORDS.get(r.get("person") or "unknown", "whose is not known")
        name = _a_name(tname)
        cite = f"{name}, as listed on the Documents tab"
        notice = r.get("type") in ("uscis_notice", "i360_approval", "i797")
        search = " ".join([r.get("type") or "", "document documents paper"])
        base = f"doc:{r['id']}"
        out.append(_passage(base, "document", f"{title}, {whose}, is on the case.", cite, {"documents"} | ({"filed"} if notice else set()), search))
        if r.get("issued"):
            out.append(_passage(f"{base}:issued", "document", f"{title} ({whose}): {ISSUED.get(r.get('issued_kind') or 'issued', 'issued')} {us(r['issued'])}.",
                                cite, {"documents"}, search))
        if r.get("expires"):
            out.append(_passage(f"{base}:expires", "document", f"{title} ({whose}): {EXPIRES.get(r.get('expires_kind') or 'expires', 'expires')} {us(r['expires'])}.",
                                cite, {"documents"}, search))
        out.append(_passage(f"{base}:language", "document", f"{title} ({whose}): in {LANG.get(r.get('language') or 'unknown', r.get('language'))}"
                            + ("; its language was assumed from the country." if r.get("language_basis") == "country" else "."),
                            cite, {"assumed"} if r.get("language_basis") == "country" else set(), search + " language"))
        if r.get("person_basis") == "only_person":
            out.append(_passage(f"{base}:whose", "document", f"{title}: whose it is was assumed: the only person on the case.", cite, {"assumed"}, search))
        out.append(_passage(f"{base}:quality", "document", f"{title}: the scan is {QUALITY.get(r.get('quality') or 'unknown', 'not measured')}.", cite, set(),
                            search + " scan quality"))
        if r.get("added"):
            out.append(_passage(f"{base}:added", "document", f"{title}: added to the case on {clock.us_date(r['added'])}.", cite, set(), search + " added"))
        for field, how in (("text", ""), ("translated", " (its English translation)")):
            lines = _joined([x.strip() for x in _hide(str(r.get(field) or "")).splitlines() if x.strip()])[:TEXT_LINES]
            for n, line in enumerate(lines, 1):
                page = rec.page_of(r, line)
                out.append(_passage(f"text:{r['id']}:{field}:{n}", "text", f"{tname}{how}, in its own words: {line}",
                                    f"{name}{how}" + (f", page {page}" if page else ", page not recorded"), set(), r.get("type") or "",
                                    day_first=(r.get("language") or "en") not in ("en", "unknown")))
    return out


def _decision_passages(rec: Record) -> list[dict[str, Any]]:
    from review.state import ACTION_WORDS, is_private_number, mask_number

    out = []
    for iid, d in rec.decisions.items():
        item = d.get("item") or {}
        keys = [k for k in item.get("facts") or [] if isinstance(k, str)]
        what = ", ".join(dict.fromkeys(fact_label(k) for k in keys[:3])) or events.plain(item.get("title") or "", 90) or "an answer"
        who = d.get("reviewer") or "someone"
        role = f" ({d['role']})" if d.get("role") else ""
        when = clock.us_date(d.get("at"))
        verb = ACTION_WORDS.get(d.get("action"), "Decided")
        values = "; ".join(f"{fact_label(k)}: {mask_number(v) if is_private_number(k, fact_label(k)) else us(v)}" for k, v in (d.get("values") or {}).items()
                           if v not in (None, ""))
        says = f"{who}{role} {verb.lower()} {what}" + (f" on {when}" if when else "") + (f". Set to: {values}" if values else "") \
            + (f". Note: {d['note']}" if d.get("note") else "") + "."
        out.append(_passage(f"decision:{iid}", "decision", says, f"the decision on {what}, by {who}" + (f", {when}" if when else ""), {"decisions"},
                            "decision decided reviewer note noted recommended " + (d.get("role") or "") + " " + " ".join(events.words(k) for k in keys),
                            at=d.get("at") or ""))
    out.sort(key=lambda p: p["at"], reverse=True)
    return out


def _journey_passages(rec: Record) -> list[dict[str, Any]]:
    """Where the case stands, its timeline, deadlines, open steps, notices, mailing records and hearings: one fact a passage (a notice's
    notice date and its priority date are two passages; a deadline and its due date are two)."""
    import documents
    import journey

    j = journey.journey(rec.dir, graph=rec.graph)
    today = clock.today().strftime("%m/%d/%Y")
    out = []
    set_by = j.get("set_by")
    where = f"where the case stands, as its records show it on {today}"
    out.append(_passage("journey:track", "journey", f"The case is on the {j['track_name']} track.", where, {"where"}, "track stands"))
    out.append(_passage("journey:stage", "journey", f"The case's stage: {j['stage_name']}.", where, {"where"}, "stage status stands"))
    out.append(_passage("journey:why", "journey", f"The stage was set by {set_by.get('by')} on {clock.us_date(set_by.get('at'))}." if set_by
                        else f"The stage, from the documents: {j.get('why')}.", where, {"where"}, "stage why"))
    for n, e in enumerate(j.get("timeline") or [], 1):
        if e.get("kind") not in ("event", "uscis", "court") or not e.get("date"):
            continue
        record = rec.by_doc.get(e.get("doc") or "")
        cite = "the case's timeline" + (f", from {_a_name(documents.name(record['type']))}, page {rec.page_of(record, '') or 1}" if record else "")
        out.append(_passage(f"timeline:{n}", "journey", f"{e.get('what')}: {us(e['date'])}", cite, {"where"}))
    cite_deadline = "the case's deadlines, as the product works them out from the record"
    for n, d in enumerate(j.get("deadlines") or [], 1):
        what = str(d.get("what") or "a deadline")
        short = what.split(" (")[0]
        out.append(_passage(f"deadline:{n}", "deadline", f"Deadline for the {d.get('owner') or 'office'}: {what}.",
                            cite_deadline + (f" ({d['source']})" if d.get("source") else ""), {"open"}, "deadline due"))
        out.append(_passage(f"deadline:{n}:due", "deadline", f"The deadline “{short}” is due on {us(d.get('date'))} ({d.get('days_left')} days from today).",
                            cite_deadline, {"open"}, "deadline due date"))
    for n, s in enumerate(j.get("steps") or [], 1):
        if s.get("done"):
            continue
        text = str(s.get("text") or "").replace(" → ", " to ").replace("→", " to ")  # the screen's arrow, in words (a PDF prints Latin-1)
        out.append(_passage(f"step:{n}", "step", f"Open step for the {s.get('owner') or 'office'}: {text}",
                            "the case's open steps", {"open"}, "open step todo next"))
    for n, x in enumerate(j.get("notices") or [], 1):
        record = rec.by_doc.get(x.get("doc") or "")
        name = _a_name(documents.name(record["type"])) if record else "a USCIS notice"
        page = rec.page_of(record, x.get("receipt")) if record else None
        kind = x.get("kind") or "notice"
        head = f"{x.get('form') or 'USCIS'} {kind} notice (receipt {x.get('receipt')})"
        cite = name + (f", page {page}" if page else "")
        dated = [(k, w) for k, w in (("date", "notice dated"), ("priority_date", "priority date"), ("due", "response due"),
                                     ("appointment", "appointment on"), ("valid_to", "valid to")) if x.get(k)]
        if not dated:
            out.append(_passage(f"notice:{n}", "notice", f"{head}.", cite, {"filed"}, "notice receipt uscis"))
        for k, w in dated:
            out.append(_passage(f"notice:{n}" + ("" if k == "date" else f":{k}"), "notice", f"{head}: {w} {us(x[k])}.", cite, {"filed"},
                                "notice receipt uscis " + w))
    status_path = rec.dir / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    for n, f in enumerate(status.get("filings") or [], 1):
        title = f.get("title") or "a filing"
        cite = f"the mailing record for {title}, recorded by {f.get('by') or 'someone'}" + (f" on {clock.us_date(f.get('at'))}" if f.get("at") else "")
        says = f"{title} was {'filed online' if f.get('online') else 'mailed'} on {us(f.get('mailed_on'))}" \
            + (f" by {f['carrier']}" if f.get("carrier") and not f.get("online") else "") + "."
        out.append(_passage(f"filing:{n}", "filing", says, cite, {"filed"}, "mailed filed sent filing"))
        if f.get("receipt"):
            out.append(_passage(f"filing:{n}:receipt", "filing", f"Receipt number for {title}: {f['receipt']}.", cite, {"filed"}, "receipt number"))
    for n, h in enumerate(j.get("hearings") or [], 1):
        says = f"Hearing ({h.get('kind') or 'court'}) on {us(h.get('date'))}" + (f" at {h['time']}" if h.get("time") else "") \
            + (f", {h['court']}" if h.get("court") else "") + (f", judge {h['judge']}" if h.get("judge") else "") + "."
        cite = f"the hearing recorded by {h.get('by') or 'someone'}" + (f" on {clock.us_date(h.get('at'))}" if h.get("at") else "")
        out.append(_passage(f"hearing:{n}", "journey", says, cite, {"open", "where"}, "hearing court judge"))
        if h.get("result"):
            out.append(_passage(f"hearing:{n}:result", "journey", f"Result of the hearing on {us(h.get('date'))}: {h['result']}.", cite, {"where"},
                                "hearing court judge result"))
    return out


def passages(client_dir: Path | Record) -> list[dict[str, Any]]:
    """The whole record of one case, as passages: [{ref, kind, says (what the model reads), cite (where it came from, in words), topics
    (the summary's parts it belongs to), search}]. Only this case's folder is read."""
    rec = client_dir if isinstance(client_dir, Record) else Record(client_dir)
    return _journey_passages(rec) + _fact_passages(rec) + _document_passages(rec) + _decision_passages(rec)


# -- retrieval ---------------------------------------------------------------------------------------------------------------------


def _terms(question: str, items: list[dict[str, Any]], graph=None) -> tuple[list[set[str]], list[str]]:
    """The question's words as groups of stems (each word with its plain synonyms), and whole names to look for (a relative's)."""
    groups: list[set[str]] = []
    phrases: list[str] = []
    words = [w for w in _words(question) if w not in STOP]
    for w in words:
        group = {_stem(x) for x in _SYN.get(w, {w})}
        if group not in groups:
            groups.append(group)
        if w in RELATIVES and graph is not None:
            for key in RELATIVES[w]:
                fact = graph.get(key)
                if fact is not None and fact.value not in (None, "") and len(str(fact.value)) > 2:
                    phrases.append(_fold(fact.value))
    return groups, list(dict.fromkeys(phrases))


def retrieve(question: str, items: list[dict[str, Any]], top: int = TOP, graph=None) -> list[dict[str, Any]]:
    """The passages that answer to the question's words, best first: each group of the question's words a passage holds counts by how
    rare it is in the record; a relative's whole name counts most. Nothing shared: []."""
    groups, phrases = _terms(question, items, graph)
    if not groups:
        return []
    stems = [_stems(p["search"]) for p in items]
    folded = [_fold(p["search"]) for p in items]
    n = len(items) or 1
    weight = []
    for g in groups:
        df = sum(1 for s in stems if g & s)
        weight.append(0.0 if df == 0 else 1.0 + (n / df) ** 0.5)
    q = set(_words(question))
    wants_docs = bool(q & {"document", "documents", "paper", "papers", "scan", "scans", "mention", "mentions", "mentioned"}) \
        or bool(re.search(r"\b(?:is|are) there\b|\bdo(?:es)? (?:she|he|they|the client) have\b|\bdo we have\b", question, re.I))
    wants_decisions = bool(q & {"decide", "decided", "decision", "decisions", "attorney", "reviewer"})
    scored = []
    for p, s, f in zip(items, stems, folded):
        score = sum(w for g, w in zip(groups, weight) if g & s)
        score += sum(3.0 + (n ** 0.5) for ph in phrases if ph and ph in f)
        if score <= 0:
            continue
        if wants_docs and p["kind"] in ("document", "text"):
            score *= 2.0 if p["kind"] == "document" else 1.5  # "is there an I-94": the document itself first
        if wants_decisions and p["kind"] == "decision":
            score *= 1.5
        scored.append((score, p))
    if not scored:
        return []
    scored.sort(key=lambda x: -x[0])
    best = scored[0][0]
    return [p for score, p in scored if score >= best * 0.5][:top]


# -- which questions go to the model: record lookups only ---------------------------------------------------------------------------

# Staff's own ways of asking what the record holds, said in words that would otherwise read like the bound ("we", "respond", "would",
# "recommend", "able to", "risk"): rewritten to a plain lookup before the tests below.
STAFF_PHRASES = [
    (re.compile(r"\bwhat would the client like to be called\b", re.I), "what name does the client use"),
    (re.compile(r"\bwhat did (?:the attorney|the paralegal|the office|[A-Z][a-z]+ [A-Z][a-z]+) (?:decide|recommend|advise|say|note|write)\w*", re.I),
     "what decision"),
    (re.compile(r"\bdo we (?:have|hold)\b", re.I), "is there"), (re.compile(r"\bwhich (\w+) do we (?:have|hold)\b", re.I), r"which \1 are there"),
    (re.compile(r"\bdid (she|he|the client|they) respond\b", re.I), r"did \1 send"), (re.compile(r"\bdid we\b", re.I), "did the office"),
    (re.compile(r"\beligibility category\b|\brisk of harm\b|\brisk flag\b", re.I), "category"),
    (re.compile(r"\bable to (read|write|speak|understand)\b", re.I), r"\1"),
    # a request, not a question: "Can you show the I-94?", "Tell me her date of birth", "I need her A-Number", "... please"
    (re.compile(r"^\s*(?:please\s+)?(?:can|could|would|will) you (?:please )?(?:show|tell|give|find|list|look up)(?: me)?\b", re.I), "show"),
    (re.compile(r"\b(?:show|tell|give|find|list) me\b", re.I), "show"), (re.compile(r"^\s*i (?:need|want|would like)\b", re.I), "show"),
    (re.compile(r"\bplease\b", re.I), ""),
    # the earlier rounds' staff lookups that read like the bound
    (re.compile(r"\bwas (?:a |an |the )?[\w-]+ recommended in the notes\b", re.I), "what do the notes say"),
    (re.compile(r"\bwould the client like\b", re.I), "what does the client ask for"),
    (re.compile(r"\ballowed visitors\b", re.I), "visitors"), (re.compile(r"\b(?:the attorney's )?advice recorded\b", re.I), "a note recorded"),
    (re.compile(r"\bqualify the answer\b", re.I), "add to the answer"),
]
# A question that asks for a judgment, never a lookup, whatever its shape: eligibility, what to do, a prediction, a deadline to work out,
# whether something matters or what follows from it, a status, the law, a question in the first or second person
VETO = re.compile("|".join([
    r"\beligib\w*", r"\bineligib\w*", r"\bqualif\w*", r"\brequirements?\b", r"\bmeets?\b", r"\bsatisf\w*", r"\bbar(?:s|red|ring)?\b", r"\b245\b",
    r"\bwaivers?\b", r"\b(?:in|out of|lawful|legal|maintain\w*|keep\w*|lose|lost) (?:her |his |their )?status\b", r"\bappl(?:y|ies) to (?:her|him|them|the client)\b",
    r"\bshould\b", r"\bought\b", r"\brecommend\w*", r"\badvi[cs]e\w*", r"\bsuggest\w*", r"\bwould\b", r"\bwe\b", r"\bneeds?\b", r"\bneeded\b",
    r"\bbest\b", r"\bbetter\b", r"\bstrateg\w*", r"\bmust\b", r"\brequired?\b", r"\bha(?:s|ve) to\b", r"\bwill\b", r"\bgoing to\b",
    r"\blikel\w*", r"\bchances?\b", r"\bodds\b", r"\bprobab\w*", r"\bpossib\w*", r"\bguarant\w*", r"\bassur\w*", r"\bexpect\w*",
    r"\bhow long\b", r"\bdeadline to\b", r"\bhow (?:many days|much time)\b", r"\btime (?:left|run|runs)\b", r"\brun out\b", r"\bages? out\b",
    r"\bcalculat\w* (?:her |his |the |a )?(?:deadline|date|days|time)", r"\bwork out\b", r"\bfigure out\b", r"\bcurrent\s*\?|\bis current\b|\bbecome current\b",
    # a judgment of a fact the record holds: lawful, valid, late, approvable, in order, met, gives her status
    r"\blawful\w*", r"\b(?:in)?valid\b", r"\blate\b", r"\boverdue\b", r"\bapprovable\b", r"\bacceptab\w*", r"\bsufficien\w*", r"\binsufficien\w*",
    r"\badequa\w*", r"\bin order\b", r"\bin time\b", r"\bmet\b", r"\breached\b", r"\bconcerns?\b", r"\bserious\b", r"\bfactors?\b",
    r"\bgives? (?:her|him|them|the client) (?:a |any )?status\b", r"\bon track\b", r"\bprove\w*", r"\brule\w* (?:her|him|them) out\b",
    r"\bbreach\w*", r"\bunauthori[sz]ed\b", r"\baccru\w*", r"\bdefective\b", r"\bdoes .{1,60}\bwork\b(?! as| at| for (?:a|an|the) (?:employer|school))",
    r"\bconvinc\w*", r"\bunconvinc\w*", r"\bdoubt\w*", r"\bin substance\b", r"\bchanges? (?:her|his|the) case\b", r"\bmean\b", r"\bdefect\w*",
    r"\bi\b(?!-)", r"\bme\b", r"\bmy\b", r"\byou\b", r"\byour\b",
    r"\bproblems?\b", r"\bissues?\b", r"\btrouble\w*", r"\bhurt\w*", r"\bharm\w*", r"\bmatters?\b", r"\baffect\w*", r"\bimpact\w*", r"\beffects?\b",
    r"\bwhat happens\b", r"\bif\b", r"\bcould\b", r"\bcan\b", r"\bmay\b", r"\bable to\b", r"\ballowed\b", r"\bpermitted\b", r"\bentitled\b",
    r"\bokay\b", r"\bok\b", r"\balright\b", r"\bfine\b", r"\bsafe\b", r"\bdanger\w*", r"\brisk\w*", r"\bstrong\b", r"\bweak\b", r"\blegal(?:ly)?\b",
    r"\blaw\b", r"\bina\b", r"\bunder the\b", r"\bstop\w*", r"\bprotect\w*", r"\bworth\b", r"\boverstay\w*", r"\bunlawful\w*", r"\bviolat\w*",
    r"\bdeport\w*", r"\bremovab\w*", r"\binadmissib\w*", r"\badmissib\w*", r"\bred flag\b", r"\bdownsides?\b", r"\bupsides?\b", r"\bcount against\b",
    r"\bobstacle\w*", r"\b(?:good|right|bad|wrong) time\b", r"\bnext (?:move|step to take)\b", r"\bsupport\b(?! under)", r"\benough\b", r"\bmissing\b",
    r"\bcomplete\b", r"\blooks?\b", r"\bforgiv\w*", r"\bcure\w*", r"\bsmart\w*", r"\bwise\b", r"\bready\b", r"\bappropriate\b", r"\bharmless\b",
    r"\bsecure\b", r"\bproper\b", r"\bexempt\w*", r"\bjeopard\w*", r"\bsuccess\w*", r"\bfail\w*", r"\bwin\b", r"\blose\b", r"\boption\w*",
]), re.I)
# A record lookup's shape: a question word, or a yes-no question about the record, the case or a person on it
LOOKUP_SHAPE = re.compile(r"^\s*(?:what|when|where|which|who|whose|how many|is there|are there|was there|were there"
                          r"|(?:is|are|was|were|has|have|does|do|did)\s+(?:the|her|his|their|this|that|these|those|she|he|they|uscis|any|a|an)\b)", re.I)
# What a lookup asks about: a document, a fact, a decision, a step, a notice, a filing, a date, a person on the case
RECORD_NOUNS = re.compile("|".join([
    r"\bi-?\d{2,4}\w*", r"\bforms?\b", r"\bpassports?\b", r"\bcertificates?\b", r"\bcards?\b", r"\bnotices?\b", r"\breceipts?\b", r"\brfe\b",
    r"\brequests?\b", r"\bdocuments?\b", r"\bpapers?\b", r"\bscans?\b", r"\bphotos?\b", r"\btranslat\w*", r"\borders?\b", r"\bdecree\b",
    r"\blicen[cs]e\b", r"\bvisas?\b", r"\bgreen card\b", r"\bwork permit\b", r"\bead\b", r"\bsocial security\b", r"\bssn\b", r"\ba-?number\b",
    r"\bnumbers?\b", r"\bnames?\b", r"\bsurname\b", r"\bcalled\b", r"\bdates?\b", r"\bbirthday\b", r"\bdob\b", r"\bborn\b", r"\bbirth\b", r"\bage\b",
    r"\baddress\w*", r"\bphone\b", r"\bemail\b", r"\bentry\b", r"\benter\w*", r"\barriv\w*", r"\badmi(?:ssion|tted)\b", r"\bclass\b", r"\bstatus\b",
    r"\bcitizenship\b", r"\bnationality\b", r"\bcountry\b", r"\bcity\b", r"\bparents?\b", r"\bfather\b", r"\bmother\b", r"\bspouse\b", r"\bhusband\b",
    r"\bwife\b", r"\bchild(?:ren)?\b", r"\bson\b", r"\bdaughter\b", r"\bsiblings?\b", r"\bbrothers?\b", r"\bsisters?\b", r"\bgrand\w+",
    r"\bemployer\b", r"\bjobs?\b", r"\bwork\w*\b", r"\bschool\b", r"\boccupation\b", r"\bheight\b", r"\bweight\b", r"\beyes?\b", r"\bhair\b",
    r"\bcolou?r\b", r"\bethnicity\b", r"\brace\b", r"\bmarital\b", r"\bmarri\w*", r"\bdivorc\w*", r"\blanguage\b", r"\benglish\b", r"\bportuguese\b",
    r"\bspanish\b", r"\bcreole\b", r"\bdecision\w*", r"\bdecided?\b", r"\bconfirm\w*", r"\bcorrect\w*", r"\bnotes?\b", r"\bnoted\b",
    r"\bsteps?\b", r"\bdeadlines?\b", r"\bdue\b", r"\bhearings?\b", r"\bcourt\b", r"\bjudge\b", r"\bmotions?\b", r"\bfilings?\b", r"\bfiled\b",
    r"\bmailed\b", r"\bsent\b", r"\bsend\b", r"\bupload\w*", r"\banswer\w*", r"\bsa(?:y|id)\b", r"\bwr(?:ote|itten)\b", r"\bsign\w*", r"\btrack\b",
    r"\bstage\b", r"\bpriority\b", r"\bcase\b", r"\bopened\b", r"\brestricted\b", r"\bapprov\w*", r"\bdenied\b", r"\barrest\w*", r"\bcrimes?\b",
    r"\bcharged?\b", r"\brecords?\b", r"\btracking\b", r"\bcarrier\b", r"\bfees?\b", r"\bpacket\b", r"\bdeclaration\b", r"\borganization\w*",
    r"\bmember\w*", r"\btravel\w*", r"\btrips?\b", r"\blived?\b", r"\bmoved?\b", r"\bremarks?\b", r"\bcategory\b", r"\bhold\b", r"\bmention\w*",
    r"\bissued\b", r"\bexpir\w*", r"\bvalid\b", r"\bhas an?\b", r"\bdeceased\b", r"\balive\b",
]), re.I)


def _staff_words(question: str) -> str:
    out = question or ""
    for pattern, plain in STAFF_PHRASES:
        out = pattern.sub(plain, out)
    return out


def vetoed(question: str) -> bool:
    """The question asks for a judgment, whatever its shape (VETO)."""
    return bool(VETO.search(_staff_words(question)))


def record_lookup(question: str) -> bool:
    """A question about something the record holds (RECORD_NOUNS), however it is phrased ("When did she enter?", "I-94?", "date of
    birth", "passport, do we have it", "Tell me her A-Number"), with nothing in it asking for a judgment (VETO). Only these go to the
    model, and the model only chooses passages."""
    text = _staff_words(question)
    return bool(RECORD_NOUNS.search(text) or LOOKUP_SHAPE.search(text)) and not VETO.search(text)


def legal_question(question: str) -> bool:
    """A question that asks for a judgment (VETO): the attorney's call. The record's passages are shown; the model is not asked."""
    return vetoed(question)


def _existence(question: str) -> bool:
    """"Is there an I-94?", "Do we have her passport?", "passport, do we have it", "SSN on file?": whether the case holds a document."""
    return bool(re.search(r"^\s*(?:is|are|was|were) there\b|\bon file\b|\bdo we have\b|\bdo we hold\b|\bany\b.{0,20}\?$", question or "", re.I))


# Of a document's own record, what answers "is there one": that the case holds it, and its dates
_HELD = re.compile(r"^doc:[0-9a-z]+(?::issued|:expires)?$")


# A document's own words that read as advice (a paper the client brought that says "The client can adjust status now"): never offered
# among the record's lines, so a document cannot put a conclusion on the screen
ADVICE = re.compile(r"\beligib\w*|\bqualif\w*|\bshould\b|\bmust\b|\brecommend\w*|\badvi[cs]e\w*|\bcan (?:adjust|apply|file|travel|work|get)\b"
                    r"|\bgoing to\b|\bwill (?:be )?approv\w*|\bguarant\w*|\bplease\b|\b(?:file|sign|submit|apply|travel)\b (?:the|now|today|for)\b", re.I)


# -- an answer: the record's own lines, chosen for the question -----------------------------------------------------------------------


def render(p: dict[str, Any]) -> str:
    """A passage as a line on screen, in its own words: every date as MM/DD/YYYY (a document from a country that writes the day first
    is read so), with "(day and month as the document writes them)" after a date whose day could be its month; a full stop at the end."""
    text = p["says"]
    out, at = [], 0
    day_first = bool(p.get("day_first"))
    for s, e, readings, amb in find_dates(text, day_first):
        out.append(text[at:s] + us(readings[0]) + (" " + AMBIGUOUS if amb and day_first else ""))
        at = e
    line = ("".join(out) + text[at:]).strip()
    return line if line.endswith((".", "?", "!")) else line + "."


def _cited(p: dict[str, Any]) -> dict[str, str]:
    return {"ref": p["ref"], "cite": p["cite"], "says": p["says"]}


def _lines(chosen: list[dict[str, Any]], limit: int = MAX_SENTENCES, notes: list[str] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """The chosen passages as lines on screen, in the record's order, each once (advice in a document's own text left out), at most
    TEXT_PER_DOCUMENT lines of any one document's own text (notes, when given, gets "And N more lines of the passport."), at most limit:
    ([{text, sources}], shortened)."""
    out: list[dict[str, Any]] = []
    per_doc: dict[str, int] = {}
    left: dict[str, list] = {}
    for p in chosen:
        if p["kind"] == "text" and ADVICE.search(p["says"].split(": ", 1)[-1]):
            continue
        text = render(p)
        if any(x["text"] == text for x in out):
            continue
        if p["kind"] == "text":
            doc = p["ref"].split(":")[1]
            per_doc[doc] = per_doc.get(doc, 0) + 1
            if per_doc[doc] > TEXT_PER_DOCUMENT:
                name = re.sub(r"(?: \(its English translation\))?, page.*$", "", p["cite"])
                left.setdefault(doc, [name, 0])[1] += 1
                continue
        out.append({"text": text, "sources": [_cited(p)]})
    if notes is not None:
        notes.extend(MORE_LINES.format(n=n, name=name) for name, n in left.values())
    return out[:limit], len(out) > limit


def choose(raw: str, n: int) -> tuple[bool, list[int]]:
    """(the model said NOT IN THE RECORD, the passage numbers it chose, 1 to n): numbers in square brackets, or an answer of numbers and
    commas alone. Prose with no brackets chooses nothing; a number it was never given is ignored. Nothing the model writes is shown."""
    text = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S).strip()[:RAW_MAX]
    bracketed = [int(x) for group in re.findall(r"\[([\d,\s]+)\]", text) for x in re.findall(r"\d+", group)]
    plain = [int(x) for x in re.findall(r"\d+", text)] if re.fullmatch(r"[\d,\s.;and]*", text) else []
    numbers = [x for x in dict.fromkeys(bracketed or plain) if 1 <= x <= n]
    return bool(re.search(r"not in the record", text, re.I)) and not numbers, numbers


def prompt(numbered: list[dict[str, Any]], question: str) -> str:
    """What the model receives: the approved practice text, word for word, then the numbered passages and the question."""
    return PRACTICE + "\n\nPassages:\n" + "\n".join(f"[{n}] {p['says']}" for n, p in enumerate(numbered, 1)) + f"\n\nQuestion: {question.strip()}\n"


def ask_model(text: str) -> tuple[str, str]:
    """(the model's answer, its name): the one place this feature calls a model (tests replace it). The same local client as the
    declaration's grammar suggestions (drafting.local_model), with a question's own budget: QUESTION_TIMEOUT seconds, never sent again
    after a timeout; an answer the model had to cut off raises drafting.ModelCutOff."""
    import drafting

    return drafting.local_model(text, NUM_PREDICT, 16384, timeout=QUESTION_TIMEOUT, retry_on_timeout=False, raise_cut_off=True)


def answer_from(question: str, found: list[dict[str, Any]], model=None, everything: list[dict[str, Any]] | None = None,
                asked: str | None = None) -> dict[str, Any]:
    """The answer to one question: {kind (answered, not_in_record, neutral, unchosen, empty, attorneys_call, unreachable, cut_off), lead,
    sentences [{text, sources}] (always the record's own lines, never the model's words), notes, refusal, chosen (passage numbers), raw
    (the model's own text, kept in the log only), model}. question: the words the rules read (English); asked: the words the model is
    shown (the staff member's own, when they asked in Portuguese or Spanish); everything: the case's passages (for a document's dates).
      - A question that asks for a judgment (VETO): ATTORNEYS_CALL and the passages found. The model is not asked.
      - Nothing found: REFUSAL, and the model is not asked.
      - Neither a record noun nor a lookup's shape: NEUTRAL and the passages found. The model is not asked.
      - Otherwise the model chooses passage numbers; the chosen passages are shown in the record's order, at most MAX_SENTENCES, at most
        TEXT_PER_DOCUMENT lines of one document's text. "Is there", "do we have": the document's line and its dates. It says NOT IN THE
        RECORD: REFUSAL. It answers nothing: EMPTY and the passages found. It chooses nothing usable: UNCHOSEN and the passages found."""
    import drafting

    out: dict[str, Any] = {"kind": "answered", "lead": None, "sentences": [], "notes": [], "refusal": None, "chosen": [], "raw": None, "model": None}

    def record(kind: str, lead: str) -> dict[str, Any]:
        notes: list[str] = []
        lines, _cut = _lines(found, 6, notes)
        return out | {"kind": kind, "lead": lead if lines else None, "refusal": None if lines else REFUSAL, "sentences": lines, "notes": notes}

    if vetoed(question):
        lines, _cut = _lines(found, 6)
        return out | {"kind": "attorneys_call", "lead": ATTORNEYS_CALL if lines else ATTORNEYS_CALL_ALONE, "sentences": lines}
    if not found:
        return out | {"kind": "not_in_record", "refusal": REFUSAL}
    if not record_lookup(question):
        return record("neutral", NEUTRAL)
    try:
        raw, name = (model or ask_model)(prompt(found, asked or question))
    except drafting.ModelCutOff:
        return out | {"kind": "cut_off", "refusal": CUT_OFF}
    except Exception:  # noqa: BLE001 -- the model failing is said, never its name or the exception's
        return out | {"kind": "unreachable", "refusal": UNREACHABLE}
    out["raw"], out["model"] = str(raw or "")[:RAW_MAX], name
    if not str(raw or "").strip():
        return record("empty", EMPTY)
    nothing, numbers = choose(raw, len(found))
    out["chosen"] = numbers
    if nothing:
        return out | {"kind": "not_in_record", "refusal": REFUSAL}
    chosen = [found[i - 1] for i in sorted(numbers)]
    if _existence(asked or question) and any(p["kind"] in ("document", "text") for p in chosen):
        # "is there one": each chosen document's own line and its dates, from the whole record, never its text
        docs = list(dict.fromkeys(p["ref"].split(":")[1] for p in chosen if p["kind"] in ("document", "text")))
        pool = everything or found
        chosen = [p for d in docs for p in pool if p["kind"] == "document" and _HELD.match(p["ref"]) and p["ref"].split(":")[1] == d] \
            + [p for p in chosen if p["kind"] not in ("document", "text")]
    notes: list[str] = []
    lines, shortened = _lines(chosen, MAX_SENTENCES, notes)
    if not lines:  # the model wrote words, or chose nothing it was given: what the record holds on it, chosen by retrieval
        return record("unchosen", UNCHOSEN)
    return out | {"sentences": lines, "notes": notes + ([SHORTENED] if shortened else [])}


def _log(client_dir: Path, entry: dict[str, Any]) -> None:
    """One line on the case's questions.jsonl (appended: nothing rewrites a line)."""
    path = Path(client_dir) / LOG
    line = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _need(who: str) -> None:
    if not (who or "").strip():
        raise ValueError("Enter your name first: every question records who asked it.")
    reason = why_not()
    if reason:
        raise ValueError(reason)


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _outcome_words(a: dict[str, Any], used: int) -> str:
    """How a question ended, for the ledger: counts and the outcome, never the question's words."""
    if a["kind"] == "attorneys_call":
        return f"the attorney's call, {_count(len(a['sentences']), 'place')} in the record shown" if a["sentences"] else "the attorney's call"
    if a["kind"] == "unreachable":
        return "the local model could not be reached"
    if a["kind"] == "cut_off":
        return "the local model's answer was cut off"
    if a["kind"] == "not_in_record":
        return "the record does not say"
    if a["kind"] == "english_only":
        return "asked in another language, which the product does not read yet"
    if a["kind"] in ("neutral", "empty"):
        return f"{_count(len(a['sentences']), 'place')} in the record shown"
    if a["kind"] == "unchosen":
        return f"the local model chose nothing, {_count(len(a['sentences']), 'place')} in the record shown"
    return f"answered with {_count(len(a['sentences']), 'line')} of the record, chosen from {_count(used, 'place')}" + (", shortened" if a["notes"] else "")


# -- a question in Portuguese or Spanish: its words, in English, by a word list ---------------------------------------------------------

# Words that tell a question's language (a question word, a pronoun, an article no English sentence has)
_LANG_WORDS = {"pt": set("quando onde qual quais quem quantos quantas ela ele dela dele tem foi nasceu entrou sua seu da do dos das nos na no "
                         "mae pai e esta existe ha".split()),
               "es": set("cuando donde cual cuales quien cuantos cuantas ella el tiene fue nacio entro su del los las la hay esta existe "
                         "madre padre es".split())}
# The record's nouns and the question words, Portuguese and Spanish to English (folded, no accents): a word list, never a translator
_GLOSSARY = {
    "pt": {"quando": "when", "onde": "where", "qual": "which", "quais": "which", "quem": "who", "quantos": "how many", "quantas": "how many",
           "entrou": "entered", "entrada": "entry", "chegou": "arrived", "chegada": "arrival", "nasceu": "born", "nascimento": "birth", "data": "date",
           "passaporte": "passport", "pai": "father", "mae": "mother", "pais": "parents", "nome": "name", "sobrenome": "family name",
           "documento": "document", "documentos": "documents", "endereco": "address", "telefone": "phone", "numero": "number",
           "certidao": "certificate", "cartao": "card", "aviso": "notice", "recibo": "receipt", "decisao": "decision", "decidiu": "decided",
           "advogado": "attorney", "advogada": "attorney", "passos": "steps", "prazo": "deadline", "prazos": "deadlines", "audiencia": "hearing",
           "tribunal": "court", "enviou": "sent", "enviado": "sent", "escola": "school", "trabalho": "work", "emprego": "job", "idioma": "language",
           "lingua": "language", "irmao": "brother", "irma": "sister", "filhos": "children", "casada": "married", "casado": "married",
           "cidadania": "citizenship", "cidade": "city", "olhos": "eyes", "cor": "color", "cabelo": "hair",
           "aprovado": "approved", "aprovacao": "approval", "preso": "arrested", "presa": "arrested", "estados": "united states", "existe": "is there",
           "ha": "is there", "tem": "is there", "avos": "grandparents",
           # words of a judgment: kept, so the question is the attorney's call in any language
           "elegivel": "eligible", "deve": "should", "pode": "can", "vai": "will", "problema": "problem", "risco": "risk", "lei": "law",
           "deportada": "deported", "deportado": "deported", "status": "status", "prova": "prove", "valido": "valid", "valida": "valid",
           "atrasado": "late", "atrasada": "late"},
    "es": {"cuando": "when", "donde": "where", "cual": "which", "cuales": "which", "quien": "who", "cuantos": "how many", "cuantas": "how many",
           "entro": "entered", "entrada": "entry", "llego": "arrived", "llegada": "arrival", "nacio": "born", "nacimiento": "birth", "fecha": "date",
           "pasaporte": "passport", "padre": "father", "madre": "mother", "padres": "parents", "nombre": "name", "apellido": "family name",
           "documento": "document", "documentos": "documents", "direccion": "address", "telefono": "phone", "numero": "number",
           "certificado": "certificate", "acta": "certificate", "tarjeta": "card", "aviso": "notice", "recibo": "receipt", "decision": "decision",
           "decidio": "decided", "abogado": "attorney", "abogada": "attorney", "pasos": "steps", "plazo": "deadline", "plazos": "deadlines",
           "audiencia": "hearing", "corte": "court", "tribunal": "court", "envio": "sent", "enviado": "sent", "escuela": "school", "trabajo": "work",
           "idioma": "language", "hermano": "brother", "hermana": "sister", "hijos": "children", "casada": "married", "casado": "married",
           "ciudadania": "citizenship", "pais": "country", "ciudad": "city", "ojos": "eyes", "color": "color", "pelo": "hair", "cabello": "hair",
           "aprobado": "approved", "aprobacion": "approval", "arrestada": "arrested", "arrestado": "arrested", "estados": "united states",
           "existe": "is there", "hay": "is there", "tiene": "is there", "abuelos": "grandparents",
           "elegible": "eligible", "debe": "should", "puede": "can", "va": "will", "problema": "problem", "riesgo": "risk", "ley": "law",
           "deportada": "deported", "deportado": "deported", "estatus": "status", "prueba": "prove", "valido": "valid", "valida": "valid",
           "tarde": "late"},
}


def question_language(question: str) -> str:
    """"pt" or "es" for a question in Portuguese or Spanish (two of its words or more say so), else "en"."""
    words = set(_WORD.findall(_fold(question)))
    scores = {lang: len(words & marks) for lang, marks in _LANG_WORDS.items()}
    lang = max(scores, key=lambda k: scores[k])
    english = len(words & {"the", "what", "when", "where", "which", "who", "is", "does", "did", "her", "his", "she", "he", "have", "of"})
    return lang if scores[lang] >= 2 and scores[lang] > english else "en"


def english_words(question: str, lang: str) -> str:
    """The question's words the word list knows, in English and in order ("Quando ela entrou nos Estados Unidos?" reads "when entered
    united states"); "" when it knows none of the record's words."""
    table = _GLOSSARY.get(lang) or {}
    out = [table[w] for w in _WORD.findall(_fold(question)) if w in table]
    return " ".join(dict.fromkeys(out)) if any(RECORD_NOUNS.search(w) or VETO.search(w) for w in out) else ""


def ask(client_dir: Path, question: str, who: str, role: str | None = None, model=None) -> dict[str, Any]:
    """Answers one question about this case from its own record, and logs it (the case's questions.jsonl and the event ledger).
    model: (prompt) -> (answer, model name); the local model by default."""
    question = " ".join(str(question or "").split())
    _need(who)
    if not question:
        raise ValueError("Type a question first.")
    if len(question) > QUESTION_MAX:
        raise ValueError(f"A question can be up to {QUESTION_MAX} characters. Ask it in fewer words.")
    client_dir = Path(client_dir)
    rec = Record(client_dir)
    items = passages(rec)
    lang = question_language(question)
    rules = english_words(question, lang) if lang != "en" else question  # what retrieval and the rules read: English words
    if not rules:  # a question in Portuguese or Spanish with none of the record's words the word list knows
        found: list[dict[str, Any]] = []
        a = {"kind": "english_only", "lead": None, "sentences": [], "notes": [], "refusal": ENGLISH_ONLY[lang], "chosen": [], "raw": None, "model": None}
    else:
        found = retrieve(rules, items, TOP, rec.graph)
        a = answer_from(rules, found, model, items, asked=question if lang != "en" else None)
    entry = {"id": secrets.token_hex(6), "at": clock.stamp(), "who": who.strip(), "role": role, "kind": "question", "question": question,
             "outcome": a["kind"], "model": a["model"], "passages": [_cited(p) for p in found], "raw": a["raw"],
             "chosen": a["chosen"], "answer": {k: a[k] for k in ("lead", "sentences", "notes", "refusal")}}
    _log(client_dir, entry)
    events.record("questions", "asked", "Asked a question about the case: " + _outcome_words(a, len(found)), case_dir=client_dir, who=who.strip(), role=role)
    return view(entry)


# -- the summary for the attorney ------------------------------------------------------------------------------------------------

# Each part: (the topic its passages carry, its title). The passages are chosen by kind, not by a model, and shown in the record's own
# words with their citations.
SUMMARY_PARTS = [
    ("who", "Who the client is"), ("where", "Track and stage"), ("filed", "What was filed and when"), ("open", "What is open"),
    ("decisions", "Decisions made"), ("documents", "Documents held"), ("assumed", "Documents with details assumed"),
]


def summary(client_dir: Path, who: str, role: str | None = None, model=None) -> dict[str, Any]:
    """The summary for the attorney, built straight from the record's passages (no model): each part is the passages of its kind (the
    client's own facts; the track, stage and timeline; the mailing records and notices; open steps, deadlines, hearings and every
    answer whose sources disagree; the decisions; the documents and their dates; what was assumed about a document), each as its own
    line with where it came from, at most SUMMARY_TOP a part (and how many more). DRAFT, shown on screen and as a PDF; never saved
    into a filing, the packet or the bundle. model: not used (kept so a caller may pass one)."""
    _need(who)
    client_dir = Path(client_dir)
    items = passages(client_dir)
    parts, used = [], 0
    for part, title in SUMMARY_PARTS:
        every = [p for p in items if part in p["topics"]]
        if part == "who":  # who the client is, in the order a person reads it: name, birth, citizenship, numbers
            every.sort(key=lambda p: WHO_KEYS.index(p["ref"].split(":", 1)[1]) if p["ref"].split(":", 1)[1] in WHO_KEYS else len(WHO_KEYS))
        distinct, _cut = _lines(every, len(every))
        lines = distinct[:SUMMARY_TOP]
        if not lines:
            answer = {"lead": None, "sentences": [], "notes": [], "refusal": NONE_RECORDED}
        else:
            more = len(distinct) - len(lines)  # distinct lines left out (the same line twice is one)
            answer = {"lead": None, "sentences": lines, "notes": [MORE.format(n=more)] if more > 0 else [], "refusal": None}
        used += len(lines)
        parts.append({"part": part, "title": title, "passages": [_cited(p) for p in every[:SUMMARY_TOP]], "answer": answer})
    entry = {"id": secrets.token_hex(6), "at": clock.stamp(), "who": who.strip(), "role": role, "kind": "summary", "question": "Summary for the attorney",
             "outcome": "answered", "parts": parts}
    _log(client_dir, entry)
    events.record("questions", "summarised", f"Made a summary for the attorney: {_count(used, 'line')} from the record", case_dir=client_dir,
                  who=who.strip(), role=role)
    return view(entry)


# -- reading the log back ----------------------------------------------------------------------------------------------------------


def entries(client_dir: Path) -> list[dict[str, Any]]:
    """Every question and summary asked on this case, oldest first (a line that is not JSON is skipped)."""
    path = Path(client_dir) / LOG
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def view(entry: dict[str, Any]) -> dict[str, Any]:
    """What the screen shows of a logged question or summary: no raw model text, no passage it did not use."""
    base = {"id": entry["id"], "at": entry["at"], "date": clock.us_date(entry["at"]), "who": entry["who"], "kind": entry["kind"],
            "question": entry["question"], "outcome": entry["outcome"]}
    if entry["kind"] == "summary":
        return base | {"note": SUMMARY_NOTE, "parts": [{"title": p["title"], "answer": p["answer"]} for p in entry.get("parts") or []]}
    return base | {"answer": entry["answer"]}


def panel(client_dir: Path, limit: int = 20) -> dict[str, Any]:
    """The case page's box: whether anyone can ask, the practice, and the latest questions asked here."""
    rows = entries(client_dir)
    return {"on": is_on(), "practice": practice(), "why_not": why_not(), "intro": INTRO, "summary_note": SUMMARY_NOTE,
            "history": [view(e) for e in reversed(rows[-limit:])], "total": len(rows)}


_LATIN1 = {"→": "to", "←": "from", "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": ":", "…": "...",
           "•": "-", " ": " "}


def latin1(text: str) -> str:
    """What the PDF's fonts can print (Latin-1): an arrow as "to", curly quotes straight, an accented letter outside Latin-1 as its
    letter, anything else as "?"."""
    out = []
    for c in str(text):
        if ord(c) < 256:
            out.append(c)
        elif c in _LATIN1:
            out.append(_LATIN1[c])
        else:
            base = "".join(x for x in unicodedata.normalize("NFKD", c) if ord(x) < 256 and not unicodedata.combining(x))
            out.append(base or "?")
    return "".join(out)


def summary_pdf(client_dir: Path, summary_id: str) -> bytes:
    """A logged summary as a PDF: DRAFT on every page, each sentence with where it came from. Drawn from the case's questions.jsonl when
    asked for; never saved beside the case's filings."""
    from pypdf import PdfWriter

    from fill.continuation import HEIGHT, MARGIN, WIDTH
    from review.bundle import _Sheet, _wrap

    if not re.fullmatch(r"[0-9a-f]{12}", str(summary_id or "")):
        raise LookupError("There is no such summary on this case.")
    entry = next((e for e in entries(client_dir) if e.get("id") == summary_id and e.get("kind") == "summary"), None)
    if entry is None:
        raise LookupError("There is no such summary on this case.")
    width = WIDTH - 2 * MARGIN
    pages: list = []
    y = 0.0

    def new_page():
        nonlocal y
        p = _Sheet()
        p.ops[:0] = ["q 0.88 g BT /F2 110 Tf 0.766 0.643 -0.643 0.766 120 190 Tm (DRAFT) Tj ET Q"]
        pages.append(p)
        y = HEIGHT - 60
        return p

    p = new_page()

    def line(text: str, font: str = "F3", size: float = 10, indent: float = 0, gap: float = 0) -> None:
        nonlocal p, y
        for part in _wrap(latin1(text), size, width - indent):
            if y < 70:
                p = new_page()
            p.text(MARGIN + indent, y, part, font, size)
            y -= size + 4
        y -= gap

    line("Summary for the attorney (DRAFT)", "F2", 15, gap=4)
    line(f"Made by {entry['who']} on {clock.us_date(entry['at'])} from this case's own record.", "F3", 9)
    line(SUMMARY_NOTE, "F3", 9, gap=10)
    for part in entry.get("parts") or []:
        line(part["title"], "F2", 12, gap=2)
        a = part["answer"]
        if a.get("refusal"):
            line(a["refusal"], "F3", 10, indent=8, gap=6)
        for s in a.get("sentences") or []:
            line(s["text"], "F3", 10, indent=8)
            line("From: " + "; ".join(src["cite"] for src in s["sources"]), "F3", 8, indent=20, gap=3)
        for note in a.get("notes") or []:
            line(note, "F3", 8, indent=8, gap=3)
        y -= 6
    for i, page in enumerate(pages):
        page.text(MARGIN, 38, "DRAFT: the case's own record, line by line; not for filing.", "F2", 8)
        page.text(WIDTH - MARGIN - 56, 26, f"Page {i + 1} of {len(pages)}", "F3", 7)
    writer = PdfWriter()
    for page in pages:
        writer.add_page(page.to_page(writer))
    writer.add_metadata({"/Title": "Summary for the attorney (DRAFT)"})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
