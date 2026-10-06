"""The firm's wording library, learned from approvals (brief L3): the office's own way of saying things comes back on the next case with the same facts.

WHAT IT HOLDS. Only text an attorney approved on a case (an explanation in Part 14, brief L2: src/part14_explain.py), or text imported from the
firm's own past filings and approved in bulk on Settings (tools/import_wordings.py). Never a model's words, never an unapproved sentence. Each
wording is the approved text with its slots abstracted: every value that came from a fact of the case is replaced by a typed slot that names it
({decision_date}, {i360_receipt}, {arrival_city}); every other date, place, name, number or receipt in the text is offered on the card as "make this
a slot?" before it is kept (and is a slot unless the attorney says keep it); the client's own identifiers (the names, the A-Number, a receipt,
the date of birth, the addresses, a phone or an e-mail address) are always slots, whatever anyone says. So a wording holds slots and never a value
from a case. A wording learned on a restricted case (src/restricted.py) is the same: slots only, every date, place, name and number a slot, and the
list of cases it was used on is shown only to people who may open each of them.

WHERE. data/wordings/<form>/<item>/<id>.json, one plain file per wording (I485_WORDINGS points elsewhere; the folder is owner-only), beside the
case folders. It is in the firm's export (src/records.py lists it: the library is the firm's asset, per office), every write is a ledger row
(kind "wordings") and docs/data_dictionary.md describes it. <form> is "i485" (the form's own Part 14 page); the store is the same for any form
or free-text box, but only Part 14 is wired. <item> is the answer the wording explains (the last part of its fact key), or "to_place" for text
from a past filing whose item the product could not place.

THE OFFER. On a new case, the "Explain the Yes answers" card offers, before the shipped DRAFT wording, the firm's approved wordings for the same
form, edition, item, voice and office, ranked by fact-pattern match first (how many of the facts the wording was approved under are true here;
a wording approved under facts this case contradicts is left out and counted), text similarity second (difflib), and use count third. Each is
shown with "the office wrote this on N cases, approved by X on <date>", its slots refilled from this case's facts with the same rule as the
shipped wording (a settled fact, each cited; a blank in square brackets where this case lacks the fact). When the attorney has switched the local
model on (Settings, Drafting and models: the case questions' switch) and approved the practice, the model may re-order the top candidates by
returning ONE NUMBER (H4's discipline: candidates in, a number out; anything else, and any word that asks for a judgment (case_questions.VETO),
is refused); it never writes a word. The paralegal's pick and any edit are recorded; an edit that is approved becomes a new version linked to its
parent. Nothing is applied without the attorney's approval on the case (src/part14_explain.py).

NEVER DELETED. A wording is retired, never deleted: it stays for the cases it was used on. A wording whose only approvals were taken back is not
offered. A past filing's text is a candidate ("from a past filing, not yet approved") and is offered on no case until an attorney approves it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator

import clock
import events

ENV = "I485_WORDINGS"
FOLDER = "wordings"
VERSION = 1
FORM = "i485"
TO_PLACE = "to_place"
STATUSES = {"approved": "Approved", "candidate": "From a past filing, not yet approved", "retired": "Retired", "discarded": "Discarded"}
ORIGINS = {"approval": "Approved on a case", "past_filing": "From a past filing"}
TOP = 5  # candidates the model may re-order
RANK_ID = "PRACTICE:WORDING-RANK"
RANK_NAME = "Choosing among the firm's own wordings with the local model"
RANK_PRACTICE = ("You help a member of the law firm's staff choose among wordings the firm's attorneys approved before for one answer on a form. Below is "
                 "what is true on this case, and numbered wordings, each approved by the firm for the same answer. Choose the one number whose wording "
                 "best fits the facts that are true on this case. Answer with that number only. Write no words and no sentences. You never write, "
                 "change or add a wording: the firm shows only its own approved wordings, and an attorney approves the one that is used.")
RANK_SOURCE = ("The firm's own practice for choosing among its approved wordings, written for the attorney's approval: no statute or rule requires it. "
               "These words are what the local model is told, before the facts and the numbered wordings")
NUM_PREDICT = 12
RANK_TIMEOUT = 60
RAW_MAX = 2000
SLOT_TYPES = {"date": "a date", "place": "a place", "name": "a name", "number": "a number", "receipt": "a receipt number", "person": "a number that identifies a person"}
# What each slot of the explanations came from (brief L2's slots: src/part14_explain.py): the fact keys, or the case page or notice it is read from
SLOT_FACTS = {"arrival_city": ["applicant.last_arrival_city"], "arrival_state": ["applicant.last_arrival_state"], "arrival_date": ["applicant.last_arrival_date"],
              "admit_until": ["applicant.i94_admit_until_date"], "i360_receipt": ["folder.notice.i360.receipt"], "i360_approved": ["folder.notice.i360.date"],
              "decision_date": ["case_page.immigration_court.decision_date"], "periods": ["questionnaire.prior_employer.dates", "folder.notice.work_permit.valid"]}
_lock_stamp = {"n": 0}  # bumped by every write in this process: a coarse file clock never serves a stale copy
_cache: dict[str, Any] = {}


# -- where -----------------------------------------------------------------------------------------------------------------------------------


def root(clients_root: str | Path) -> Path:
    """I485_WORDINGS, else data/wordings beside the case folders."""
    env = os.environ.get(ENV)
    return Path(env) if env else Path(clients_root).resolve().parent / FOLDER


def case_root(client_dir: str | Path) -> Path:
    return root(Path(client_dir).resolve().parent)


def item_folder(key: str) -> str:
    """The folder a wording is kept in: the answer's own name (applicant.part9.worked_without_authorization -> worked_without_authorization)."""
    return re.sub(r"[^a-z0-9_]+", "_", str(key or "").rsplit(".", 1)[-1].lower()).strip("_") or TO_PLACE


def _path(base: Path, rec: dict[str, Any]) -> Path:
    return base / rec["form"] / (item_folder(rec["key"]) if rec.get("key") else TO_PLACE) / f"{rec['id']}.json"


def _stamp(base: Path) -> tuple:
    if not base.is_dir():
        return (None, _lock_stamp["n"])
    stamps = [base.stat().st_mtime_ns]
    for form in sorted(p for p in base.iterdir() if p.is_dir()):
        stamps.append(form.stat().st_mtime_ns)
        stamps += [p.stat().st_mtime_ns for p in sorted(form.iterdir()) if p.is_dir()]
    return (tuple(stamps), _lock_stamp["n"])


def every(base: Path) -> list[dict[str, Any]]:
    """Every wording in the library, oldest first (read again when a file or a folder changes). Callers read it and never change it."""
    key = str(base)
    stamp = _stamp(base)
    if _cache.get(key, (None, None))[0] == stamp:
        return _cache[key][1]
    out = []
    if base.is_dir():
        for path in sorted(base.glob("*/*/*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict) and rec.get("id") and rec.get("text") is not None:
                out.append(rec)
    out.sort(key=lambda r: (r.get("created") or "", r["id"]))
    _cache[key] = (stamp, out)
    return out


def get(base: Path, wording_id: str) -> dict[str, Any] | None:
    return next((r for r in every(base) if r["id"] == wording_id), None)


def _write(base: Path, rec: dict[str, Any]) -> None:
    """One wording, written whole (a temporary file, then a rename), the folder owner-only."""
    path = _path(base, rec)
    path.parent.mkdir(parents=True, exist_ok=True)
    for folder in (base, path.parent.parent, path.parent):
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
    rec["version"] = VERSION
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    old = next((p for p in (base / rec["form"]).glob(f"*/{rec['id']}.json") if p != path), None)  # an item placed after the fact moves the file
    if old is not None:
        old.unlink()
    _lock_stamp["n"] += 1


def _ledger(base: Path, action: str, what: str, who: str | None, role: str | None) -> None:
    events.record("wordings", action, what, home=base.parent, who=who, role=role, version=VERSION)


def _need(who: str) -> str:
    who = str(who or "").strip()
    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    return who


def _wording_id(form: str, edition: str, key: str, voice: str, text: str) -> str:
    return "w-" + hashlib.sha256("\n".join([form, edition or "", key or "", voice or "", text]).encode("utf-8")).hexdigest()[:12]


# -- the words slots are shown in ------------------------------------------------------------------------------------------------------------


def slot_words(name: str) -> str:
    """A slot in words: the explanations' own ("the date of the judge's decision") or a free slot's type ("a date")."""
    import part14_explain as px

    spoken = px.shipped()["slots"].get(name)
    if spoken:
        return spoken
    return SLOT_TYPES.get(_free_type(name) or "", "something to fill in")


def _free_type(name: str) -> str | None:
    m = re.fullmatch(r"(date|place|name|number|receipt|person)_\d+", name)
    return m.group(1) if m else None


def tokens_of(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\{(\w+)\}", text or "")))


def spoken(text: str) -> str:
    """The text as a person reads it: each slot as its words in square brackets."""
    return re.sub(r"\{(\w+)\}", lambda m: f"[{slot_words(m.group(1))}]", text or "")


def slots_of(text: str) -> list[dict[str, Any]]:
    """The slots a text holds: {name, words, kind (fact: an explanation's slot filled from a fact of the case; typed: written by a person each time;
    free: a date, place, name or number the case's facts do not fill), type, facts (the fact keys the slot comes from)}."""
    import part14_explain as px

    out = []
    for name in tokens_of(text):
        if name in px.shipped()["slots"]:
            out.append({"name": name, "words": slot_words(name), "kind": "fact" if name in SLOT_FACTS else "typed", "type": None, "facts": SLOT_FACTS.get(name, [])})
        else:
            kind = _free_type(name)
            out.append({"name": name, "words": slot_words(name), "kind": "free", "type": kind, "facts": []})
    return out


# -- abstraction: the text with its slots -------------------------------------------------------------------------------------------------


_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
_DATE_FORMS = re.compile(
    r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4}"
    rf"|(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})\.?,?\s+\d{{4}}|(?:{_MONTHS})\.?,?\s+\d{{4}})\b"
    r"|\b(?:19|20)\d{2}\b", re.I)
_FORCED = [("receipt", re.compile(r"\b[A-Z]{3}\s?\d{10}\b")), ("person", re.compile(r"\bA[- ]?\d{3}[- ]?\d{3}[- ]?\d{2,3}\b", re.I)),
           ("person", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")), ("person", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
           ("person", re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)")), ("person", re.compile(r"(?<![\w{])[A-Z]{1,2}\d{6,9}(?!\w)", re.I)),  # a passport or other document number: one or two letters, then six to nine digits
           ("number", re.compile(r"(?<![\w{])\d{6,}(?!\w)"))]
_ID_KEY = re.compile(r"given|family|middle|(?:^|[._])name|a_number|dob|birth_date|date_of_birth|ssn|passport|travel_document|document_number|birth_city|street|(?:^|[._])zip|postal|apt|phone|email|receipt|i94_number"
                     r"|employer|school|city|spouse|father|mother|parent|principal|in_care_of|alien|license|physical|mailing|address", re.I)
_NAME_KEY = re.compile(r"given|family|middle|(?:^|[._])name|principal|spouse|father|mother|parent|in_care_of|employer|school", re.I)
_NOT_ID = re.compile(r"arrival|country|(?:^|[._])state|class|manner|(?:^|[._])has_|(?:^|[._])is_|same_as|admit|since|unit_type|^firm\.|^office\.|folder\.notice|p14_|part14_"
                     r"|^applicant\.part9\.|date_from|date_to|_date_used|_used$|^intake\.", re.I)
_DOB_KEY = re.compile(r"dob|birth_date|date_of_birth", re.I)
# Words that open a sentence and are not a name: the first word of a sentence is looked at like any other word, and is a name unless it is one of these
# (or one of the words of the firm's own wordings)
_OPENERS = frozenset("""then later however because after before when while since once today yesterday tomorrow afterwards also finally first second third next now here there
my our his her their this that these those it we they he she one two some many most all not in on at by for from with during as if so but and or to although though unless until
every each both either neither another other such only even still just already soon never always often sometimes usually later again moreover therefore thus instead meanwhile""".split())
_PLAIN = frozenset({"yes", "no", "none", "n/a", "na", "usa", "us", "united states", "male", "female", "unknown", "other", "single", "married"})
_NEVER_WORDS = frozenset("""a an the and or but if of to in on at by for from with about as into over under than then that this these those is are was were be been
has have had do does did not no yes i me my we our you your he she they them his her their it its who whom which what when where while after before since until
during also only more most some any each every other another such same own very can could would should may might will shall must""".split())
_LEGAL = frozenset("""united states america u.s. usa special immigrant juvenile sij form forms part item page immigration court judge board appeals bia eoir department
homeland security dhs uscis ina notice appear nta order removal petition application adjust adjustment status i-360 i-485 i-94 i-765 attorney applicant respondent
client office decision decided proceedings""".split())
_cached_stop: dict[str, frozenset] = {}


def _stop_words() -> frozenset:
    """The capitalized words of the shipped wordings: a sentence the firm ships is never offered as "make this a slot?"."""
    import part14_explain as px

    stamp = px.SCHEMA.stat().st_mtime_ns
    if _cached_stop.get("stamp") != stamp:
        data = px.shipped()
        texts = [w["text"][v] for w in data["wordings"] for v in w["text"]] + [s["text"][v] for s in data["sentences"].values() for v in s["text"]]
        words = {m.lower() for t in texts for m in re.findall(r"[A-Za-z][A-Za-z'’.-]*", re.sub(r"\{\w+\}", " ", t))}
        _cached_stop.update(stamp=stamp, words=frozenset(words | _NEVER_WORDS | _LEGAL))
    return _cached_stop["words"]


def _date_variants(value: Any) -> set[str]:
    """A date of birth written the ways a person writes it: 2007-04-02, 04/02/2007, 4/2/2007, April 2, 2007, 2 April 2007, 2007."""
    text = str(value or "")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        m2 = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if not m2:
            return {" ".join(text.split())} if re.search(r"\d{4}", text) else set()  # a date written some other way is kept as written; a word (Yes, No) is not a date
        mo, d, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
    else:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    if not 1 <= mo <= 12:
        return {" ".join(text.split())} if re.search(r"\d{4}", text) else set()
    month = names[mo - 1]
    return {f"{y:04d}-{mo:02d}-{d:02d}", f"{mo:02d}/{d:02d}/{y:04d}", f"{mo}/{d}/{y}", f"{d:02d}/{mo:02d}/{y:04d}", f"{d}/{mo}/{y}", f"{month} {d}, {y}", f"{month} {d} {y}",
            f"{month[:3]} {d}, {y}", f"{d} {month} {y}", f"{d} de {month} de {y}", f"{y}-{mo}-{d}"}


def identity(graph, extra: dict[str, str] | None = None) -> dict[str, Any]:
    """What identifies the people of a case, read from its fact graph (and from `extra`, name-box values read from a filled form): {phrases (whole values),
    tokens (the words of a name), dates (a date of birth in every way it is written)}. Everything here is replaced whatever the person says."""
    phrases: set[str] = set()
    tokens: set[str] = set()
    dates: set[str] = set()
    values: list[tuple[str, str]] = [(k, v) for k, v in (extra or {}).items()]
    if graph is not None:
        for key, fact in graph.all_facts().items():
            if not _ID_KEY.search(key) or _NOT_ID.search(key):
                continue
            seen = [fact.value] + [x for s in fact.sources for x in (s.raw_value, s.normalized_value)]
            values += [(key, v) for v in seen if isinstance(v, (str, int, float)) and str(v).strip()]
    for key, raw in values:
        v = " ".join(str(raw).split())
        if _DOB_KEY.search(key):
            dates |= _date_variants(v)
            continue
        if v.lower() in _PLAIN | _NEVER_WORDS or len(v) < 3 or re.fullmatch(r"[A-Za-z]{2}", v) or re.fullmatch(r"(?:true|false)", v, re.I):
            continue
        phrases.add(v)
        if _NAME_KEY.search(key):
            tokens |= {t for t in re.findall(r"[^\W\d_]{3,}", v) if t.lower() not in _NEVER_WORDS | _LEGAL and t.lower() not in _PLAIN}
    return {"phrases": sorted(phrases, key=len, reverse=True), "tokens": sorted(tokens, key=len, reverse=True), "dates": sorted(dates, key=len, reverse=True)}


def _loose(value: str) -> str:
    """A value as a pattern that finds it however its spaces and case fall (a whole word, not a part of one)."""
    return r"(?<![\w{])" + r"\s+".join(re.escape(w) for w in value.split()) + r"(?![\w}])"


class _Text:
    """A text and the places in it that are slots: a replacement is made over the whole text at once, from the last place to the first."""

    def __init__(self, text: str):
        self.text = text
        self.free: dict[tuple[str, str], str] = {}
        self.counts: dict[str, int] = {}
        self.forced: list[dict[str, str]] = []

    def slot_for(self, kind: str, value: str) -> str:
        found = self.free.get((kind, value.lower()))
        if found:
            return found
        self.counts[kind] = self.counts.get(kind, 0) + 1
        name = f"{kind}_{self.counts[kind]}"
        self.free[(kind, value.lower())] = name
        return name

    def protected(self) -> list[tuple[int, int]]:
        return [m.span() for m in re.finditer(r"\{\w+\}", self.text)]

    def replace_all(self, pattern: str | re.Pattern, token: Callable[[re.Match], str | None], flags: int = re.I) -> int:
        rx = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
        done = 0
        out, last = [], 0
        for m in rx.finditer(self.text):
            if any(a < m.end() and m.start() < b for a, b in self.protected()):
                continue
            put = token(m)
            if put is None:
                continue
            out.append(self.text[last:m.start()] + "{" + put + "}")
            last = m.end()
            done += 1
        out.append(self.text[last:])
        self.text = "".join(out)
        return done


_RUN = re.compile(r"(?<![\w{-])[A-Z][A-Za-z'’]*(?![\w}]|-\d)(?:(?:,\s+(?=[A-Z][A-Za-z'’]{2})|\s+(?:(?:de|da|do|dos|das|del|of|the|and)\s+)?)[A-Z][A-Za-z'’]*(?![\w}]|-\d))*")


def _runs(text: str, protected: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    """The capitalized runs of a text a person might make a slot of, a place or a name: a sentence's first word too, unless it is a word that opens
    sentences; never the words of the firm's own wordings (the form, the court, the petition)."""
    stop = _stop_words()
    found = []
    for m in _RUN.finditer(text):
        a, b = m.span()
        if any(x < b and a < y for x, y in protected):
            continue
        words = list(re.finditer(r"\S+", m.group(0)))
        before = text[:a].rstrip(" \"'(“")
        if not before or before[-1] in ".!?:\n":  # a sentence's first word is a name like any other unless it is a word that opens sentences
            while words and words[0].group(0).lower().strip(".'’,") in stop | _OPENERS:
                words = words[1:]
        if not words or all(w.group(0).lower().strip(".'’") in stop for w in words):
            continue
        found.append((a + words[0].start(), b, text[a + words[0].start():b]))
    return found


# a word right before one of these names a place (a court's city, an office's), never a person: offered to keep as text, never blanked unasked (the
# Implementation note.
_PLACE_WORDS = r"(?:Immigration\s+Court|Court|Field\s+Office|Asylum\s+Office|Office|County|Detention\s+Center|Port\s+of\s+Entry|Airport|Border)"
_PLACE_AFTER = re.compile(r"\s+" + _PLACE_WORDS + r"\b", re.I)  # "Boston" followed by "Immigration Court"
_PLACE_END = re.compile(r"\b" + _PLACE_WORDS + r"$", re.I)  # the run "Boston Immigration Court" itself


def _leftovers(text: str) -> list[tuple[int, int, str, str]]:
    """What a text still holds that a person might make a slot of: [(start, end, the words, date | number | place | name)] in order, not overlapping.
    A slot already in the text ({decision_date}) is not looked at."""
    prot = [m.span() for m in re.finditer(r"\{\w+\}", text)]
    spans: list[tuple[int, int, str, str]] = []

    def free(a: int, b: int) -> bool:
        return not any(x < b and a < y for x, y in prot + [(s[0], s[1]) for s in spans])

    for m in _DATE_FORMS.finditer(text):
        if free(m.start(), m.end()) and m.group(0).lower() not in _stop_words():
            spans.append((m.start(), m.end(), m.group(0), "date"))
    for m in re.finditer(r"(?<![\w{.-])\d+(?:[.,]\d+)?(?![\w}-]|\.\d)", text):
        if free(m.start(), m.end()) and not re.search(r"(?:\b(?:item|part|page|form|section|number|no|line)\.?\s*|-)$", text[:m.start()], re.I):
            spans.append((m.start(), m.end(), m.group(0), "number"))
    for a, b, run in _runs(text, prot + [(s[0], s[1]) for s in spans]):
        before = text[:a].rstrip()
        kind = ("place" if re.search(r"\b(?:in|at|near|from|to|of|before|born in|lived in|moved to)(?:\s+the)?$", before, re.I)
                or re.search(r"^,\s*[A-Z]{2}\b", text[b:b + 6]) or _PLACE_AFTER.match(text[b:b + 40]) or _PLACE_END.search(run) else "name")  # "the Boston Immigration Court": a place
        spans.append((a, b, run, kind))
    spans.sort()
    out, last = [], 0
    for s in spans:
        if s[0] >= last:
            out.append(s)
            last = s[1]
    return out


def abstract(text: str, *, facts: dict[str, Any] | None = None, graph=None, restricted: bool = False, choices: dict[str, str] | None = None,
             templates: list[str] | None = None, extra_identity: dict[str, str] | None = None) -> dict[str, Any]:
    """The approved text with its slots: {text, slots, offers [{token, type, words, kind, choice}], forced [{type, words}], masked (how many places were
    made slots), kept (what the attorney chose to keep as text)}.

    1. every value that came from a fact of the case (facts: slot name -> value) becomes that slot;
    2. a slot a person wrote each time (the judge's decision, how the case ended) is found by laying the text over the wordings it came from
       (templates: wordings with {slots}, the shipped sentences or the wording that was offered) and becomes that slot;
    3. the case's own identifiers (identity(graph)), receipts, A-Numbers, Social Security numbers, e-mail addresses, phone numbers and long
       numbers become free slots, always;
    4. every remaining date, place, name and number is a slot; only a place is offered ("make this a slot?"), and stays text only when choices[token] is
       "keep" (a date, a number or a person's name never stays); a restricted case (or a past filing) keeps none."""
    work = _Text(str(text or ""))
    # 1. the case's facts
    for name, value in sorted((facts or {}).items(), key=lambda kv: -len(str(kv[1] or ""))):
        v = " ".join(str(value or "").split())
        if len(v) >= 2 and not v.startswith("["):
            work.replace_all(_loose(v), lambda m, n=name: n)
    # 2. what a person wrote each time
    for template in templates or []:
        _typed(work, template)
    # 3. who the people are, whatever anyone says
    ids = identity(graph, extra_identity)
    for d in ids["dates"]:
        work.replace_all(_loose(d), lambda m: work.slot_for("person", m.group(0)))
    for phrase in ids["phrases"]:
        work.replace_all(_loose(phrase), lambda m: work.slot_for("name" if re.search(r"[^\W\d_]{3}", phrase) and not re.search(r"\d", phrase) else "person", m.group(0)))
    for token in ids["tokens"]:
        work.replace_all(_loose(token), lambda m: work.slot_for("name", m.group(0)))
    for kind, rx in _FORCED:
        work.replace_all(rx, lambda m, k=kind: work.slot_for(k, m.group(0)))
    forced = sum(work.counts.values())
    # 4. what is left of dates, places, names and numbers
    offers: list[dict[str, Any]] = []
    blanked: list[dict[str, Any]] = []
    out, last, kept = [], 0, []
    for a, b, token, kind in _leftovers(work.text):
        keepable = kind == "place" and not restricted  # only a place (a court, an office, a city) may stay as text: a date, a number, a person's name never
        keep = keepable and (choices or {}).get(token) == "keep"
        (offers if keepable else blanked).append({"token": token, "type": kind, "words": SLOT_TYPES[kind], "choice": "keep" if keep else "slot"})
        if keep:
            kept.append(token)
            continue
        out.append(work.text[last:a] + "{" + work.slot_for(kind, token) + "}")
        last = b
    out.append(work.text[last:])
    final = _numbered("".join(out))
    unique = list({(o["token"].lower(), o["type"]): o for o in offers}.values())
    return {"text": final, "slots": slots_of(final), "offers": unique, "blanked": list({(o["token"].lower(), o["type"]): o for o in blanked}.values()), "forced": forced,
            "kept": kept, "masked": len(tokens_of(final))}


def _numbered(text: str) -> str:
    """The free slots (a date, place, name, number or receipt no fact fills) numbered in the order they stand in the text, each kind from 1, so a wording
    reads the same whichever order the product found them in."""
    names: dict[str, str] = {}
    count: dict[str, int] = {}

    def renumber(m: re.Match) -> str:
        name = m.group(1)
        kind = _free_type(name)
        if kind is None:
            return m.group(0)
        if name not in names:
            count[kind] = count.get(kind, 0) + 1
            names[name] = f"{kind}_{count[kind]}"
        return "{" + names[name] + "}"

    return re.sub(r"\{(\w+)\}", renumber, text)


def _typed(work: _Text, template: str) -> None:
    """Lays one wording over the text: what stands where one of its slots does is the slot (a person wrote it): the judge's decision, how a case ended.
    A template that does not fit the text (it was rewritten) finds nothing."""
    import part14_explain as px

    names = tokens_of(template)
    if not any(n in px.TYPED for n in names):
        return
    parts, last, groups = [], 0, {}
    for m in re.finditer(r"\{(\w+)\}", template):
        parts.append(re.escape(template[last:m.start()]).replace(r"\ ", r"\s+"))
        name = m.group(1)
        if name == "cite":
            parts.append(r"(?:\s+under\s+[^,.]{1,80}?)?")
        elif name in px.TYPED and name not in groups:
            groups[name] = f"g{len(groups)}"
            parts.append(rf"(?:\{{{name}\}}|(?P<{groups[name]}>[^.]{{1,300}}?))")
        else:
            parts.append(rf"(?:\{{{name}\}}|[^.]{{1,120}}?)")
        last = m.end()
    parts.append(re.escape(template[last:]).replace(r"\ ", r"\s+"))
    m = re.search("".join(parts), work.text, re.S)
    if not m:
        return
    spans = sorted(((m.start(g), m.end(g), n) for n, g in groups.items() if m.group(g)), reverse=True)
    for a, b, name in spans:
        said = work.text[a:b]
        if said.strip() and not re.fullmatch(r"\[[^\]]*\]", said.strip()):
            work.text = work.text[:a] + "{" + name + "}" + work.text[b:]


def leaks(text: str, graph=None, restricted: bool = False, extra_identity: dict[str, str] | None = None) -> list[str]:
    """What of a person is left in a wording (the guard before it is kept; empty when none): the case's own identifiers, receipts, A-Numbers, Social
    Security numbers, e-mail addresses, phone numbers, document numbers and long numbers; any date, any number and any name left in the text; on a restricted
    case also any place."""
    held = re.sub(r"\{\w+\}", " ", text or "")
    found = []
    ids = identity(graph, extra_identity)
    for d in ids["dates"]:
        if re.search(_loose(d), held, re.I):
            found.append("a date of birth")
    for phrase in ids["phrases"]:
        if re.search(_loose(phrase), held, re.I):
            found.append("something that identifies a person on the case")
    for token in ids["tokens"]:
        if re.search(_loose(token), held, re.I):
            found.append("a name from the case")
    for kind, rx in _FORCED:
        if rx.search(held):
            found.append({"receipt": "a receipt number", "person": "a number or address that identifies a person", "number": "a long number"}[kind])
    left = _leftovers(text or "")
    if any(x[3] != "place" for x in left):
        found.append("a date, a number or a name")
    if restricted and left:
        found.append("a date, number, place or name from a restricted case")
    return list(dict.fromkeys(found))


# -- the pattern: what was true on the case a wording was approved on --------------------------------------------------------------------------

_COURT = {"Decision: relief granted": "court:relief_granted", "Decision: removal ordered or relief denied": "court:removal_ordered_or_denied",
          "Removal ordered in absentia": "court:ordered_in_absentia", "Case terminated or dismissed": "court:terminated"}
_TOKEN_WORDS = {"nta": "a Notice to Appear is in the folder", "i360_approved": "the Form I-360 is approved", "ewi": "the last arrival was without admission or parole",
                "admit_until": "the I-94 gives the date the authorized stay ended", "court:pending": "the client is in immigration court and no result is recorded",
                "court:relief_granted": "the court record shows relief granted", "court:removal_ordered_or_denied": "the court record shows removal ordered or relief denied",
                "court:ordered_in_absentia": "the court record shows a removal order in absentia", "court:terminated": "the court record shows the case terminated or dismissed"}
BOOLEANS = ("nta", "i360_approved", "ewi", "admit_until")


def case_tokens(p: dict[str, Any], yes_keys: list[str], key: str) -> list[str]:
    """What is true on a case, as tokens: the facts that chose the shipped wording (src/part14_explain.py _pattern) and the other answers that say Yes."""
    import part14_explain as px

    out = []
    if p.get("nta"):
        out.append("nta")
    if p.get("i360") is not None:
        out.append("i360_approved")
    if p.get("ewi"):
        out.append("ewi")
    if px._iso(p.get("admit_until")) is not None:
        out.append("admit_until")
    court = p.get("court") or {}
    out += sorted({_COURT[r["outcome"]] for r in court.get("results") or [] if r["outcome"] in _COURT})
    if not court.get("results") and court.get("in_court"):
        out.append("court:pending")
    out += [f"yes:{item_folder(k)}" for k in sorted(yes_keys) if k != key]
    return out


def pattern_of(p: dict[str, Any], yes_keys: list[str], key: str) -> dict[str, list[str]]:
    """{present: the tokens true on the case, absent: the facts the wordings are chosen by that were not}."""
    present = case_tokens(p, yes_keys, key)
    absent = [b for b in BOOLEANS if b not in present]
    if not any(t.startswith("court:") for t in present):
        absent.append("court")
    return {"present": present, "absent": absent}


def conflicts(pattern: dict[str, Any], here: list[str]) -> tuple[bool, int]:
    """(whether the case says the opposite of what the wording was approved under, how many facts differ): the court's record is what a wording says
    happened, so another result (or a result where the wording was written for none, or none where it was written for one) is the opposite and the
    wording is left out; so is a wording written where the I-360 was approved on a case that has no approval notice (it says the petition was approved); a Notice to Appear, an approved I-360, a last arrival, an I-94 date that is true here and was not true there (or the other way
    round) is a difference, counted, and the wording is ranked below the ones that differ less."""
    there_court = {t for t in pattern.get("present") or [] if t.startswith("court:")}
    here_court = {t for t in here if t.startswith("court:")}
    opposite = bool((there_court and there_court != here_court) or ("court" in (pattern.get("absent") or []) and here_court)
                    or ("i360_approved" in (pattern.get("present") or []) and "i360_approved" not in here))  # such a wording says the petition was approved
    differ = len([a for a in pattern.get("absent") or [] if a in here and a != "court"])
    return opposite, differ


def pattern_words(tokens: list[str]) -> list[str]:
    import part14_explain as px

    out = []
    for t in tokens:
        if t in _TOKEN_WORDS:
            out.append(_TOKEN_WORDS[t])
        elif t.startswith("yes:"):
            q = next((x["question"] for x in px.listed() if item_folder(x["key"]) == t[4:]), "")
            out.append(f"the client also answers Yes: {q}" if q else "another answer is Yes")
    return out


# -- uses --------------------------------------------------------------------------------------------------------------------------------------


_reader: ContextVar = ContextVar("wordings_reader", default=None)


@contextmanager
def reading_as(may_open: Callable[[str], bool] | None) -> Iterator[None]:
    """For the length of a request: the person reading may open the cases may_open says (ReviewApp.may_open). A restricted case a reader may not open is
    absent from everything this module shows them: no count, no use, no "the office wrote this on N cases", no fact list, no approver or date from it, no
    case name; and a wording whose only uses are such cases is absent altogether (not listed, not counted, not offered)."""
    token = _reader.set(may_open)
    try:
        yield
    finally:
        _reader.reset(token)


def _may(may_open: Callable[[str], bool] | None = None) -> Callable[[str], bool] | None:
    return may_open if may_open is not None else _reader.get()


def live(rec: dict[str, Any], may_open: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    """The uses that still stand, on the cases the reader may open (every case when no reader is named: the product's own work)."""
    may = _may(may_open)
    return [u for u in rec.get("uses") or [] if not u.get("withdrawn") and (may is None or may(u["case"]))]


def cases_of(rec: dict[str, Any], may_open: Callable[[str], bool] | None = None) -> list[str]:
    return sorted({u["case"] for u in live(rec, may_open)})


def count(rec: dict[str, Any], may_open: Callable[[str], bool] | None = None) -> int:
    return len(cases_of(rec, may_open))


def hidden(rec: dict[str, Any], may_open: Callable[[str], bool] | None = None) -> bool:
    """Absent for this reader: every case the wording was ever used on (even one whose approval was taken back) is one the reader may not open."""
    may = _may(may_open)
    uses = rec.get("uses") or []
    return bool(may is not None and uses and not any(may(u["case"]) for u in uses))


def offerable(rec: dict[str, Any], may_open: Callable[[str], bool] | None = None) -> bool:
    """Approved, and either approved in bulk from a past filing or used on a case whose approval still stands (and that the reader may open)."""
    return rec.get("status") == "approved" and (rec.get("origin") == "past_filing" or count(rec, may_open) > 0)


def approval_of(rec: dict[str, Any], may_open: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """Who approved the wording and when, as this reader may see it: the approval it was learned from, unless that was on a case the reader may not open,
    then the first approval on a case they may open."""
    may = _may(may_open)
    first = (rec.get("uses") or [{}])[0]
    if rec.get("origin") == "past_filing" or may is None or not first.get("case") or may(first["case"]):
        return rec.get("approved") or {}
    mine = next((u for u in rec.get("uses") or [] if not u.get("withdrawn") and may(u["case"])), None)
    return {"who": mine["by"], "role": mine.get("role"), "at": mine["at"]} if mine else {}


def origin_visible(rec: dict[str, Any], may_open: Callable[[str], bool] | None = None) -> bool:
    """The case the wording was learned on is one the reader may open (so the facts it was approved under may be shown)."""
    may = _may(may_open)
    first = (rec.get("uses") or [{}])[0]
    return rec.get("origin") == "past_filing" or may is None or not first.get("case") or may(first["case"])


def _us(at: str | None) -> str:
    return clock.us_date(at) if at else ""


def wrote_words(rec: dict[str, Any], may_open: Callable[[str], bool] | None = None) -> str:
    """"The office wrote this on 4 cases, approved by Sam Attorney on 10/05/2026." (the cases and the approval this reader may see)"""
    n = count(rec, may_open)
    by = approval_of(rec, may_open)
    who = f"approved by {by.get('who')} on {_us(by.get('at'))}" if by.get("who") else "approved"
    if n == 0:
        return f"From the office's past filings, {who}. Not used on a case here yet."
    return f"The office wrote this on {n} case{'s' if n != 1 else ''}, {who}."


# -- refilling a wording from this case's facts ----------------------------------------------------------------------------------------------


def refill(rec: dict[str, Any], graph, p: dict[str, Any], work: dict[str, Any] | None = None) -> dict[str, Any]:
    """The wording's slots filled from this case with the explanations' own rule (a settled fact, cited; else a blank in square brackets):
    {text, slots [{name, words, value, proposed, wait, why, sources}], blanks, waits}."""
    import part14_explain as px

    names = tokens_of(rec["text"])
    if "periods" in names and work is None:
        work = px._work(graph)
    slots = {}
    for n in names:
        if n in px.shipped()["slots"]:
            slots[n] = px._slot(n, graph, p, work or {})
        else:
            slots[n] = {"name": n, "words": slot_words(n), "value": None, "proposed": None, "wait": None, "sources": [],
                        "why": f"This wording had {SLOT_TYPES.get(_free_type(n) or '', 'something')} here that no fact of the case fills: write it."}
    text = re.sub(r"\{(\w+)\}", lambda m: (slots[m.group(1)]["value"] or f"[{slots[m.group(1)]['words']}]") if m.group(1) in slots else m.group(0), rec["text"])
    waits = list(dict.fromkeys(s["wait"] for s in slots.values() if s.get("wait")))
    return {"text": text, "slots": list(slots.values()), "blanks": px.BLANK.findall(text), "waits": waits}


# -- the offer ---------------------------------------------------------------------------------------------------------------------------------


def offers(client_dir: Path, key: str, p: dict[str, Any], graph, voice: str, shipped_text: str = "", work: dict[str, Any] | None = None,
           yes_keys: list[str] | None = None) -> dict[str, Any]:
    """The firm's wordings for this item, best first: {offers, left_out (approved wordings this case's facts contradict), ranked (the local model's
    re-ordering, when one was asked for and still holds), model (whether it may be asked, and why not)}. Same form, edition, item, voice and office."""
    import offices
    import part14_explain as px

    client_dir = Path(client_dir)
    base = case_root(client_dir)
    ed = px.edition()
    office = offices.for_case(client_dir)["id"]
    here = case_tokens(p, yes_keys if yes_keys is not None else [x["key"] for x in px.answered_yes(graph)], key)
    alike = [r for r in every(base) if r["form"] == FORM and r.get("edition") == ed and r.get("key") == key and r.get("office") == office
             and not hidden(r) and offerable(r)]
    pool = [r for r in alike if r.get("voice") == voice]
    other_voice = len(alike) - len(pool)  # Implementation note.
    kept, left_out = [], 0
    for r in pool:
        opposite, differ = conflicts(r.get("pattern") or {}, here)
        if opposite:
            left_out += 1
            continue
        filled = refill(r, graph, p, work)
        present = (r.get("pattern") or {}).get("present") or []
        match = len([t for t in present if t in here])
        ratio = difflib.SequenceMatcher(None, filled["text"], shipped_text or "").ratio() if shipped_text else 0.0
        now = [t for t in (r.get("pattern") or {}).get("absent") or [] if t in here and t != "court"]
        kept.append({"id": r["id"], "text": filled["text"], "slots": filled["slots"], "blanks": filled["blanks"], "waits": filled["waits"], "match": match,
                     "of": len(present), "differ": differ, "similarity": round(ratio, 3), "uses": count(r), "voice": r.get("voice"), "origin": r.get("origin"),
                     "number": r.get("number") or 1, "parent": r.get("parent"), "wrote": wrote_words(r), "by": approval_of(r).get("who"),
                     "date": _us(approval_of(r).get("at")), "facts": pattern_words([t for t in present if t in here]),
                     # what was true on a case the reader may not open is not shown (it still ranks the wording)
                     "lacks": pattern_words([t for t in present if t not in here]) if origin_visible(r) else [], "now": pattern_words(now) if origin_visible(r) else [],
                     "ranked_by": "facts"})
    # best first: the fewest facts that differ, then the most of its facts true here, then the closest text to the shipped one, then the most cases
    kept.sort(key=lambda o: (o["differ"], -o["match"], -(o["match"] / o["of"] if o["of"] else 0), -o["similarity"], -o["uses"], o["id"]))
    rec = px.read(client_dir)
    ranked = (rec.get("ranked") or {}).get(key)
    if ranked and ranked.get("facts") == here and set(ranked.get("candidates") or []) == {o["id"] for o in kept[:TOP]}:
        if ranked.get("order"):  # the model numbered one: it goes first, the rest keep the order of the facts
            first = {i: n for n, i in enumerate(ranked["order"])}
            kept = sorted(kept[:TOP], key=lambda o: first[o["id"]]) + kept[TOP:]
            for o in kept[:TOP]:
                o["ranked_by"] = "model"
        ranked = {k: ranked.get(k) for k in ("chosen", "refused", "model", "by")}  # the last ask, and why it chose nothing when it did not
    else:
        ranked = None
    on, why = rank_on()
    return {"offers": kept, "left_out": left_out, "left_out_voice": other_voice, "ranked": ranked, "model": {"on": on, "why": why, "can": on and len(kept) > 1}}


# -- the local model: a number among the firm's own candidates -----------------------------------------------------------------------------------


def practice_entry() -> dict[str, Any]:
    """The practice as the approval catalog lists it (src/rules/approval.py)."""
    text = {"plain_text": RANK_PRACTICE, "source": RANK_SOURCE}
    return {"id": RANK_ID, "kind": "practice", "code": RANK_ID.split(":", 1)[1], "name": RANK_NAME, **text,
            "hash": hashlib.sha256(json.dumps(text, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()}


def rank_on() -> tuple[bool, str]:
    """(whether the local model may be asked to re-order the offers, why not in words): the attorney's switch (the case questions' own, Settings, Drafting
    and models) and the approval of the practice above."""
    import case_questions
    from review.state import rule_info

    if not case_questions.is_on():
        return False, "The local model is off. An attorney can switch it on in Settings (Drafting and models)."
    state = rule_info(RANK_ID)["approval"]["state"]
    if state != "approved":
        return False, ("The practice for choosing among the firm's wordings " + ("changed since" if state == "changed" else "is not yet") + " approved: an attorney approves it in Settings "
                       "(Drafting and models) before the model is asked.")
    return True, ""


def ask_model(text: str) -> tuple[str, str]:
    """(the model's answer, its name): the one place the library calls a model (tests replace it). The same local client as the declaration's grammar
    suggestions and the case questions (drafting.local_model)."""
    import drafting

    return drafting.local_model(text, NUM_PREDICT, 8192, timeout=RANK_TIMEOUT, retry_on_timeout=False, raise_cut_off=True)


def prompt(here: list[str], numbered: list[dict[str, Any]]) -> str:
    """What the model receives: the approved practice, word for word, what is true on the case, then the numbered wordings as the firm keeps them (with
    their slots, so no value of the case is in it)."""
    facts = pattern_words(here) or ["nothing more is recorded"]
    return (RANK_PRACTICE + "\n\nTrue on this case:\n" + "\n".join(f"- {f}" for f in facts) + "\n\nWordings:\n"
            + "\n".join(f"[{n}] {spoken(o['abstract'])}" for n, o in enumerate(numbered, 1)) + "\n")


def choose(raw: str, n: int) -> tuple[int | None, str]:
    """(the candidate number, why nothing was chosen): only a bare number among the candidates counts. Words, a number the model was never given, or
    a word that asks for a judgment (case_questions.VETO) choose nothing."""
    import case_questions

    text = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S).strip()[:RAW_MAX]
    if not text:
        return None, "the model answered nothing"
    m = re.fullmatch(r"\[?\s*(\d{1,2})\s*\]?\s*[.]?", text)
    if not m:
        return None, ("the model wrote a judgment, not a number" if case_questions.VETO.search(text) else "the model wrote words, not a number")
    number = int(m.group(1))
    return (number, "") if 1 <= number <= n else (None, "the model gave a number that is not one of the wordings")


def rank(client_dir: Path, key: str, who: str, role: str | None, model: Callable | None = None, graph=None) -> dict[str, Any]:
    """Asks the local model to put the top candidates in order of fit (only when the attorney switched it on and approved the practice): it returns
    ONE number, the chosen wording goes first, the rest keep the order of the facts. The answer is kept on the case (the candidates, the number, the
    model's own text, who asked) and a ledger row says it was asked. Nothing the model writes is ever shown."""
    import part14_explain as px

    who = _need(who)
    on, why = rank_on()
    if not on:
        raise ValueError(why)
    client_dir = Path(client_dir)
    if graph is None:
        from review.state import reviewed_graph

        graph = reviewed_graph(client_dir)
    e = next((x for x in px.entries(client_dir, graph) if x["key"] == key), None)
    if e is None:
        raise LookupError("That answer is not a Yes on this case's I-485 that needs an explanation.")
    offered = e["firm"]["offers"]
    top = offered[:TOP]
    if len(top) < 2:
        raise ValueError("There is only one firm wording for this answer: nothing to put in order.")
    base = case_root(client_dir)
    here = case_tokens(px._pattern(client_dir, graph), [x["key"] for x in px.answered_yes(graph)], key)
    numbered = [{"id": o["id"], "abstract": (get(base, o["id"]) or {}).get("text", "")} for o in top]
    outcome: dict[str, Any] = {"candidates": [o["id"] for o in top], "chosen": None, "refused": "", "raw": "", "model": None, "facts": here,
                               "by": {"who": who, "role": role, "at": clock.stamp()}}
    try:
        raw, name = (model or ask_model)(prompt(here, numbered))
        outcome["raw"], outcome["model"] = str(raw or "")[:RAW_MAX], name
        number, outcome["refused"] = choose(raw, len(top))
        if number:
            outcome["chosen"] = top[number - 1]["id"]
    except Exception:  # noqa: BLE001 -- the model failing is said plainly, never its name or the exception's
        outcome["refused"] = "the local model could not be reached"
    if outcome["chosen"]:
        outcome["order"] = [outcome["chosen"]] + [i for i in outcome["candidates"] if i != outcome["chosen"]]
    rec = px.read(client_dir)
    rec.setdefault("ranked", {})[key] = outcome
    px._save(client_dir, rec, "ranked", f"Asked the local model to put the firm's wordings for Part 9, item {e['item']} in order of fit"
             + ("" if outcome["chosen"] else " (it chose nothing)"), who, role)
    return outcome


# -- learning from an approval ---------------------------------------------------------------------------------------------------------------


def _origin_facts(client_dir: Path, e: dict[str, Any], graph, p: dict[str, Any]) -> dict[str, str]:
    """The values this case's facts gave to the slots the approved text was made from (the shipped wording's slots and the firm wording that was picked):
    {slot: value}, settled, or the client's own answer a person used. Only these: a city or a date a person typed that happens to equal a fact is not that fact."""
    import part14_explain as px

    names = {s["name"] for s in (e.get("suggestion") or {}).get("slots") or []}
    pick = e.get("pick")
    held = get(case_root(client_dir), pick["wording"]) if pick else None
    names |= set(tokens_of(held["text"])) if held else set()
    work = px._work(graph)
    out = {}
    for name in sorted(names):
        if name in px.TYPED or name not in px.shipped()["slots"]:
            continue
        s = px._slot(name, graph, p, work)
        value = s.get("value") or s.get("proposed")
        if value:
            out[name] = value
    return out


def preview(client_dir: Path, e: dict[str, Any], graph=None) -> dict[str, Any]:
    """What the card shows before the attorney approves: the text as the library would keep it, the places it offers to make slots ("make this a slot?"),
    and how many places were always made slots (the client's own identifiers and the facts of the case)."""
    import part14_explain as px
    from review.state import reviewed_graph
    import restricted

    client_dir = Path(client_dir)
    graph = graph if graph is not None else reviewed_graph(client_dir)
    p = px._pattern(client_dir, graph)
    done = abstract(e["text"], facts=_origin_facts(client_dir, e, graph, p), graph=graph, restricted=restricted.is_restricted(client_dir), templates=_templates(client_dir, e))
    return {"text": done["text"], "spoken": spoken(done["text"]), "offers": done["offers"], "blanked": done["blanked"], "always": done["forced"], "restricted": restricted.is_restricted(client_dir)}


def _templates(client_dir: Path, e: dict[str, Any]) -> list[str]:
    """The wordings the approved text may have come from: the shipped sentences used (in the entry's voice) and the firm wording that was picked."""
    import part14_explain as px

    out = []
    s = e.get("suggestion")
    if s:
        data = px.shipped()
        by_id = {w["id"]: w for w in data["wordings"]} | {k: v | {"id": k} for k, v in data["sentences"].items()}
        out += [by_id[i]["text"][e["voice"]] for i in s["sentences"] if i in by_id]
    pick = e.get("pick")
    if pick:
        picked = get(case_root(client_dir), pick["wording"])
        if picked:
            out.append(picked["text"])
    return out


def learn(client_dir: Path, e: dict[str, Any], who: str, role: str | None, at: str, graph=None, choices: dict[str, str] | None = None) -> dict[str, Any]:
    """Keeps what an attorney just approved on a case as a firm wording: {how (new, used, version, not_kept), id, why}. Never raises: the approval stands
    whatever happens here (an approval that cannot be kept says why on the card)."""
    try:
        return _learn(Path(client_dir), e, who, role, at, graph, choices)
    except Exception as exc:  # noqa: BLE001 -- the approval is made; a wording that could not be kept is said, and the library is unchanged
        import sys

        sys.stderr.write(f"A firm wording was not kept ({type(exc).__name__}: {exc})\n")
        return {"how": "not_kept", "id": None, "why": "The wording could not be kept in the firm's library."}


def _learn(client_dir: Path, e: dict[str, Any], who: str, role: str | None, at: str, graph, choices: dict[str, str] | None) -> dict[str, Any]:
    import offices
    import part14_explain as px
    import restricted
    from review.state import reviewed_graph

    graph = graph if graph is not None else reviewed_graph(client_dir)
    base = case_root(client_dir)
    shut = restricted.is_restricted(client_dir)
    p = px._pattern(client_dir, graph)
    pick = e.get("pick")
    parent = get(base, pick["wording"]) if pick else None
    unchanged = bool(pick and parent and pick.get("text") == e["text"])
    case = client_dir.name
    use = {"case": case, "at": at, "by": who, "role": role}
    if unchanged:
        _use(base, parent, use | {"via": "offer"})
        return {"how": "used", "id": parent["id"], "why": ""}
    done = abstract(e["text"], facts=_origin_facts(client_dir, e, graph, p), graph=graph, restricted=shut, choices=choices, templates=_templates(client_dir, e))
    problem = leaks(done["text"], graph, shut)
    if problem:
        _ledger(base, "not_kept", f"A firm wording was not kept: its text still held {problem[0]}", who, role)
        return {"how": "not_kept", "id": None, "why": f"Not kept in the library: the text still holds {problem[0]}. Make it a slot or take it out, then approve again."}
    office = offices.for_case(client_dir)
    wid = _wording_id(FORM, px.edition() or "", e["key"], e["voice"], done["text"])
    found = get(base, wid)
    if found is not None:
        if found.get("status") in ("retired", "discarded"):
            return {"how": "not_kept", "id": wid, "why": "This wording was retired by an attorney: it stays retired."}
        if found.get("status") == "candidate":  # the same words came from a past filing: this approval is the attorney's approval of them
            found = json.loads(json.dumps(found)) | {"status": "approved", "approved": {"who": who, "role": role, "at": at}}
            found.setdefault("history", []).append({"at": at, "who": who, "what": "approved on a case"})
            _write(base, found)
        _use(base, found, use | {"via": "typed"})
        if pick and parent and parent["id"] != wid:
            _edited(base, parent, use | {"became": wid})
        return {"how": "used", "id": wid, "why": ""}
    yes_keys = [x["key"] for x in px.answered_yes(graph)]
    rec = {"id": wid, "form": FORM, "edition": px.edition(), "key": e["key"], "part": e["part"], "item": e["item"], "page": e["page"], "voice": e["voice"],
           "office": office["id"], "office_name": office["name"], "text": done["text"], "slots": done["slots"], "pattern": pattern_of(p, yes_keys, e["key"]),
           "status": "approved", "origin": "approval", "created": at, "approved": {"who": who, "role": role, "at": at}, "parent": parent["id"] if parent else None,
           "number": (parent.get("number") or 1) + 1 if parent else 1, "uses": [use | {"via": "learned"}], "edits": [], "history": [{"at": at, "who": who, "what": "approved on a case"}]}
    _write(base, rec)
    _ledger(base, "learned", f"Kept a firm wording for Part {e['part']}, item {e['item']}" + (" as a new version of an earlier one" if parent else ""), who, role)
    if parent:
        _edited(base, parent, use | {"became": wid})
    return {"how": "version" if parent else "new", "id": wid, "why": ""}


def _use(base: Path, rec: dict[str, Any], use: dict[str, Any]) -> None:
    rec = json.loads(json.dumps(rec))
    if any(u["case"] == use["case"] and not u.get("withdrawn") for u in rec.get("uses") or []):
        return
    rec.setdefault("uses", []).append(use)
    rec.setdefault("history", []).append({"at": use["at"], "who": use["by"], "what": "used on another case"})
    _write(base, rec)


def _edited(base: Path, rec: dict[str, Any], edit: dict[str, Any]) -> None:
    rec = json.loads(json.dumps(rec))
    rec.setdefault("edits", []).append(edit)
    _write(base, rec)


def withdraw(client_dir: Path, key: str, who: str, role: str | None) -> int:
    """The approval of this case's explanation was taken back: the wordings kept from it stop counting this case (and a wording nobody else approved
    is no longer offered). The wording itself stays. How many were changed."""
    base = case_root(client_dir)
    case = Path(client_dir).name
    changed = 0
    for r in every(base):
        if r.get("key") != key or not any(x["case"] == case and not x.get("withdrawn") for x in (r.get("uses") or []) + (r.get("edits") or [])):
            continue
        rec = json.loads(json.dumps(r))
        for u in (rec.get("uses") or []) + (rec.get("edits") or []):  # a use stops counting, and so does an edit made on that case
            if u["case"] == case and not u.get("withdrawn"):
                u["withdrawn"] = {"who": who, "role": role, "at": clock.stamp()}
        rec.setdefault("history", []).append({"at": clock.stamp(), "who": who, "what": "the approval on a case was taken back"})
        _write(base, rec)
        changed += 1
    if changed:
        _ledger(base, "withdrawn", "An approval a firm wording was kept from was taken back on a case", who, role)
    return changed


# -- the library: what an attorney reads and decides on Settings ---------------------------------------------------------------------------------


def _item_words(rec: dict[str, Any]) -> str:
    import part14_explain as px

    if rec.get("key"):
        listed = next((x for x in px.listed(rec.get("edition")) if x["key"] == rec["key"]), None)
        if listed:
            return f"Part {listed['part']}, item {listed['item']}: {listed['question']}"
        return f"Part {rec.get('part') or '?'}, item {rec.get('item') or '?'}"
    return "To place: the item could not be found" + (f" (Part {rec['part']}, item {rec['item']} in the {rec['edition']} edition)" if rec.get("part") and rec.get("edition") else "")


def row(rec: dict[str, Any], may_open: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """One wording for the screen, as this reader may see it: only the cases the reader may open are counted or named (a restricted case they may not open
    is in no number, no list, no approval line and no fact list); a past filing's file is named only when the reader may open the case it is named for."""
    may = _may(may_open)
    cases = cases_of(rec, may)
    from_file = rec.get("from_file") or ""
    stem = from_file.split("/")[-1].split(".")[0] if from_file else ""
    picks = [u for u in live(rec, may) if u.get("via") == "offer"]
    edits = [e for e in rec.get("edits") or [] if not e.get("withdrawn") and (may is None or may(e["case"]))]
    shown = approval_of(rec, may)
    return {"id": rec["id"], "form": rec["form"], "key": rec.get("key"), "item": _item_words(rec), "edition": rec.get("edition"), "voice": rec.get("voice"),
            "office": rec.get("office_name") or rec.get("office"), "text": rec["text"], "spoken": spoken(rec["text"]), "slots": slots_of(rec["text"]),
            "status": rec["status"], "status_words": STATUSES.get(rec["status"], rec["status"]), "origin": ORIGINS.get(rec.get("origin"), ""), "origin_id": rec.get("origin"),
            "by": shown.get("who"), "date": _us(shown.get("at")), "cases": len(cases), "case_list": cases,
            "picked": len(picks) + len(edits), "edited": len(edits),
            "share": round(100 * len(edits) / (len(picks) + len(edits))) if picks or edits else None,
            "wrote": wrote_words(rec, may) if rec["status"] == "approved" else "",
            "parent": rec.get("parent"), "number": rec.get("number") or 1,
            "file": (from_file if may is None or not stem or may(stem) else "") if from_file else "", "retired": rec.get("retired"),
            "pattern": pattern_words((rec.get("pattern") or {}).get("present") or []) if origin_visible(rec, may) else [], "history": rec.get("history") or [],
            "needs_voice": not rec.get("voice")}


def library(base: Path, may_open: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """Settings, Firm wordings: every wording of the current edition by answer, those of other editions, those to place, and the candidates from past
    filings waiting for the attorney. A wording whose only uses are cases the reader may not open is not in it."""
    import part14_explain as px

    ed = px.edition()
    recs = [row(r, may_open) for r in every(base) if not hidden(r, may_open)]
    mine = [r for r in recs if r["key"] and r["edition"] == ed and r["status"] != "candidate"]
    groups: dict[str, dict[str, Any]] = {}
    order = {x["key"]: n for n, x in enumerate(px.listed())}
    for r in sorted(mine, key=lambda r: (order.get(r["key"], 999), r["number"], r["id"])):
        groups.setdefault(r["key"], {"key": r["key"], "item": r["item"], "wordings": []})["wordings"].append(r)
    return {"edition": ed, "items": list(groups.values()), "candidates": [r for r in recs if r["status"] == "candidate" and r["key"] and r["edition"] == ed],
            "to_place": [r for r in recs if r["status"] == "candidate" and (not r["key"] or r["edition"] != ed)] + [r for r in recs if r["status"] in ("approved", "retired") and (not r["key"] or r["edition"] != ed)],
            "total": len(recs), "items_to_place": [{"key": x["key"], "words": f"Part {x['part']}, item {x['item']}: {x['question']}"} for x in px.listed()]}


def report_rows(base: Path, may_open: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    """Reports, Firm wordings: per answer, each approved or retired wording, its use count and the share of cases where the paralegal edited it after
    picking it. Counts and the wording's own slots only: no case is named, and a case the reader may not open is in no count (a wording whose only uses are
    such cases is not a row)."""
    import part14_explain as px

    order = {x["key"]: n for n, x in enumerate(px.listed())}
    out = []
    for r in sorted((r for r in every(base) if r["status"] in ("approved", "retired") and r.get("key") and not hidden(r, may_open)),
                    key=lambda r: (order.get(r["key"], 999), r.get("number") or 1, r["id"])):
        v = row(r, may_open)
        out.append({"item": v["item"], "wording": spoken(r["text"])[:160] + ("…" if len(spoken(r["text"])) > 160 else ""), "voice": "The office's" if r.get("voice") == "office" else "The client's",
                    "status": v["status_words"], "approved": f"{v['by']} on {v['date']}" if v["by"] else "", "cases": v["cases"], "picked": v["picked"],
                    "edited": f"{v['share']}%" if v["share"] is not None else ""})
    return out


# -- what an attorney does with the library ------------------------------------------------------------------------------------------------------


def _attorney(role: str | None, what: str) -> None:
    if role == "paralegal":
        raise PermissionError(f"Only an attorney {what}.")


def _reid(base: Path, rec: dict[str, Any]) -> None:
    """The wording's id from what it now is (form, edition, answer, voice, text): two wordings that say the same are one wording."""
    new = _wording_id(rec["form"], rec.get("edition") or "", rec.get("key") or "", rec.get("voice") or "", rec["text"])
    if new != rec["id"] and get(base, new) is not None:
        raise ValueError("The firm already has this wording for that answer.")
    rec["id"] = new


def _save_moved(base: Path, before: dict[str, Any], rec: dict[str, Any]) -> None:
    """Writes the wording and takes away the file it was in when its id or its answer changed."""
    old = _path(base, before)
    _write(base, rec)
    if old != _path(base, rec) and old.exists():
        old.unlink()
        _lock_stamp["n"] += 1


def _change(base: Path, ids: list[str], who: str, role: str | None, act: Callable[[dict[str, Any]], str | None], action: str, said: str) -> dict[str, Any]:
    done = 0
    for wid in ids:
        before = get(base, wid)
        if before is None:
            raise LookupError("No such wording.")
        rec = json.loads(json.dumps(before))
        what = act(rec)
        if what is None:
            continue
        rec.setdefault("history", []).append({"at": clock.stamp(), "who": who, "what": what})
        _save_moved(base, before, rec)
        done += 1
    if done:
        _ledger(base, action, said.format(n=done, s="" if done == 1 else "s"), who, role)
    return {"changed": done}


def approve_candidates(base: Path, ids: list[str], who: str, role: str | None, voice: str | None = None) -> dict[str, Any]:
    """The attorney approves wordings imported from past filings (in bulk): each is offered on cases from now on. A wording with no item cannot be approved
    (place it first); one whose voice could not be told needs the voice chosen."""
    who = _need(who)
    _attorney(role, "approves a firm wording")
    now = clock.stamp()

    def act(rec: dict[str, Any]) -> str | None:
        if rec["status"] != "candidate":
            return None
        if not rec.get("key"):
            raise ValueError("A wording with no item cannot be approved: place it on its item first.")
        if rec.get("edition") != _edition():
            raise ValueError("This wording is for another edition of the form: place it on an item of the current edition first.")
        if not rec.get("voice"):
            if voice not in ("client", "office"):
                raise ValueError("Say which voice this wording is written in (the client's or the office's) before approving it.")
            rec["voice"] = voice
            _reid(base, rec)
        rec["status"], rec["approved"] = "approved", {"who": who, "role": role, "at": now}
        return "approved in bulk from a past filing" if rec.get("origin") == "past_filing" else "approved"

    return _change(base, ids, who, role, act, "approved", "Approved {n} firm wording{s} from past filings or other editions")


def _edition() -> str | None:
    import part14_explain as px

    return px.edition()


def retire(base: Path, ids: list[str], who: str, role: str | None, why: str = "") -> dict[str, Any]:
    """An approved wording is retired: no longer offered, kept for the cases it was used on."""
    who = _need(who)
    _attorney(role, "retires a firm wording")
    now = clock.stamp()

    def act(rec: dict[str, Any]) -> str | None:
        if rec["status"] != "approved":
            return None
        rec["status"], rec["retired"] = "retired", {"who": who, "role": role, "at": now, "why": str(why or "")[:300]}
        return "retired" + (f": {str(why)[:300]}" if why else "")

    return _change(base, ids, who, role, act, "retired", "Retired {n} firm wording{s}")


def discard(base: Path, ids: list[str], who: str, role: str | None) -> dict[str, Any]:
    """A candidate from a past filing the attorney does not want: it is set aside, never offered; the file stays."""
    who = _need(who)
    _attorney(role, "sets aside a firm wording")
    now = clock.stamp()

    def act(rec: dict[str, Any]) -> str | None:
        if rec["status"] != "candidate":
            return None
        rec["status"], rec["retired"] = "discarded", {"who": who, "role": role, "at": now, "why": "not wanted"}
        return "set aside, not wanted"

    return _change(base, ids, who, role, act, "discarded", "Set aside {n} firm wording{s} from past filings")


def edit_candidate(base: Path, wording_id: str, text: str, who: str, role: str | None) -> dict[str, Any]:
    """The attorney's edit of a candidate before approving it: the text is abstracted again (a date, place, name or number typed in becomes a slot), and
    the candidate keeps its place."""
    who = _need(who)
    _attorney(role, "edits a firm wording")
    before = get(base, wording_id)
    if before is None:
        raise LookupError("No such wording.")
    if before["status"] != "candidate":
        raise ValueError("Only a wording that is not approved yet can be edited here: an approved wording changes by a new approval on a case.")
    text = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines())
    if not text:
        raise ValueError("Write the wording first.")
    done = abstract(text, restricted=True)
    rec = json.loads(json.dumps(before))
    rec.update(text=done["text"], slots=done["slots"])
    _reid(base, rec)
    rec.setdefault("history", []).append({"at": clock.stamp(), "who": who, "what": "edited before approval"})
    _save_moved(base, before, rec)
    _ledger(base, "edited", "Edited a firm wording from a past filing before approving it", who, role)
    return {"id": rec["id"]}


def place(base: Path, wording_id: str, key: str, who: str, role: str | None) -> dict[str, Any]:
    """Puts a wording whose item could not be placed (or one of another edition) on an item of the current edition: it becomes a candidate, for the
    attorney to approve (the item's numbers change between editions, so the approval is made again)."""
    import part14_explain as px

    who = _need(who)
    _attorney(role, "places a firm wording")
    before = get(base, wording_id)
    if before is None:
        raise LookupError("No such wording.")
    spot = next((x for x in px.listed() if x["key"] == key), None)
    if spot is None:
        raise ValueError("That answer is not one the form says to explain in this edition.")
    if before["status"] == "discarded":
        raise ValueError("This wording was set aside.")
    rec = json.loads(json.dumps(before))
    rec.update(key=key, edition=px.edition(), part=spot["part"], item=spot["item"], page=spot["page"], form=FORM)
    now = clock.stamp()
    if before["status"] == "candidate":
        _reid(base, rec)
        rec.setdefault("history", []).append({"at": now, "who": who, "what": f"placed on Part {spot['part']}, item {spot['item']}"})
        _save_moved(base, before, rec)
        _ledger(base, "placed", f"Placed a firm wording on Part {spot['part']}, item {spot['item']}", who, role)
        return {"id": rec["id"]}
    # an approved or retired wording of another edition stays as it is, for the cases it was used on; a new candidate, linked to it, is made for this edition
    rec.update(status="candidate", parent=before["id"], number=(before.get("number") or 1) + 1, uses=[], edits=[], approved=None, retired=None,
               created=now, history=[{"at": now, "who": who, "what": "carried to the current edition from an earlier approval"}])
    _reid(base, rec)
    _write(base, rec)
    _ledger(base, "placed", f"Carried a firm wording to Part {spot['part']}, item {spot['item']} of the current edition", who, role)
    return {"id": rec["id"]}


def export_all(base: Path, may_open: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """The whole library as one document an attorney can take away, in words (no code of ours): every wording with its text and slots, the answer it explains,
    who approved it and when, what was true on the case it was written on, how often it was used, picked and edited, and its history. The cases are named
    only for the reader who may open them. The same files are in the firm's export (tools/export_firm.py --everything)."""
    out = []
    for r in every(base):
        if hidden(r, may_open):
            continue
        v = row(r, may_open)
        out.append({"answer": v["item"], "edition_of_the_form": v["edition"], "voice": {"client": "first person (Yes, I ...)", "office": "third person (The applicant ...)"}.get(v["voice"] or "", "not known"),
                    "office": v["office"], "status": v["status_words"], "where_it_came_from": v["origin"], "text": v["text"], "text_as_read": v["spoken"],
                    "approved_by": v["by"], "approved_on": v["date"], "used_on_cases": v["cases"], "cases": v["case_list"], "times_picked": v["picked"], "times_edited_after_picking": v["edited"],
                    "version": v["number"], "started_from": v["parent"], "true_where_it_was_approved": v["pattern"], "past_filing": v["file"], "retired": v["retired"], "history": v["history"],
                    "id": v["id"]})
    return {"about": "The firm's wording library: plain text with its slots in curly brackets, who approved each wording and when. No value of any client is in it.", "wordings": out}
