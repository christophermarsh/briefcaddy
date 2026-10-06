"""The engagement with the client, from the agreement to the end of the case.

    The agreement     the engagement letter and fee agreement, per filing and per office: the firm's own wording (Settings, Firm documents: one
                      copy per office, with a version and who edited it; shipped as DRAFT English with machine drafts in Portuguese, Spanish and
                      Haitian Creole, schemas/firm/firm_documents.json), filled in from the case with the client's name, the filings, the office, the
                      fee the firm types (never a guessed fee) and the date. The client signs it on the portal (reading it in their language
                      with the English beside it, typing their name under the declaration the portal already uses) or on paper (the office
                      uploads the scan and the date); an attorney counter-signs it on the case page.
    Declining         a non-engagement letter: the firm did not take the case. No attorney-client relationship was formed, and the deadlines
                      are the person's own to watch. The portal's link stops working the day of the letter.
    Withdrawing       a disengagement letter: the firm withdraws, the client ends it, or the client moves to another lawyer. The date the
                      representation ends, what the firm returns, the deadlines the case knows of, and that the person must watch them.
    Closing           a closing letter: the matter is concluded, what the firm keeps and until when, how to get the file. The file returned
                      to the client is the export of the case's records and documents as a zip the office hands over (logged).

A case is open, declined, withdrawn, transferred or closed (STATES). Every end state is the attorney's (a paralegal is refused), with a reason,
and every one can be reopened by an attorney. An ended case leaves every work list (My work, What's due, the dashboard's deadlines), stays in
Search and in All clients under a filter, and keeps its restriction (src/restricted.py is never touched here).

The old office period and operational end provide only a planning proposal. Legal representation completion, jurisdiction applicability,
retention and destruction require separate current case-specific attorney determinations (client_file_policy.py). Protective handover
has its own reviewed inventory and recipient authority; unresolved retention alone does not prevent it. No elapsed planning clock authorizes
destruction. A manual destruction record and Q1 both require current destruction authority; Q1 retains its wait and second-attorney gates.

Nothing here says a letter meets a rule. The letters' wording is the attorney's to approve, recorded like a rule's approval (PRACTICE_ID,
src/rules/approval.py), and every letter says it is a DRAFT until it is approved; an unapproved agreement is never sent to be signed.

Records: data/clients/<id>/engagement.json (this module), engagement/<letter>.pdf beside it; the portal's copy for the client
(data/portal/clients/<id>/engagement.json: the letter to sign, the signature, the end of the case; src/portal/store.py); the firm's letters
(data/firm_documents.json) and the destruction record (data/destroyed.json). Every write appends a row to the event ledger (src/events.py).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import threading
from datetime import date
from pathlib import Path
from typing import Any

import clock
import events
import schema_path
import client_file
import client_file_policy
from portal.communication_consent import data_mutation

REPO = Path(__file__).resolve().parents[1]
SHIPPED = schema_path.path("firm", "firm_documents")
FILE = "engagement.json"
FOLDER = "engagement"
FIRM_FILE = "firm_documents.json"
DESTROYED_FILE = "destroyed.json"
VERSION = 1
LANGS = ("en", "pt", "es", "ht")
LANGUAGE_NAMES = {"en": "English", "pt": "Portuguese", "es": "Spanish", "ht": "Haitian Creole"}
TEXT_MAX = 4000  # characters in one typed part of a letter (the fee, the additions)
_LOCK = threading.Lock()

# What each letter is, in the staff's words.
KINDS = {"engagement": "Engagement letter and fee agreement", "non_engagement": "Non-engagement letter (the firm did not take the case)",
         "disengagement": "Disengagement letter (the representation ends)", "closing": "Closing letter"}
STATES = {"open": "Open", "declined": "Declined", "withdrawn": "Withdrawn", "transferred": "Transferred", "closed": "Closed"}
ENDED = ("declined", "withdrawn", "transferred", "closed")
LETTER_FOR = {"declined": "non_engagement", "withdrawn": "disengagement", "transferred": "disengagement", "closed": "closing"}
# Why nothing goes to a client whose case ended (portal/notify.py held: the same "Not sent." line as a conflict check's hold).
ENDED_HELD = ("The client's case with the office has ended (declined, withdrawn, transferred or closed): nothing goes to them, and no sign-in link "
              "is made. The office reaches them itself (Agreement and closing).")
STATE_WORDS = {"declined": "The firm did not take the case", "withdrawn": "The representation ended", "transferred": "The client moved to another lawyer",
               "closed": "The matter is concluded"}

# The words in square brackets a letter may hold (Settings, Firm documents), and what fills each in.
PLACEHOLDERS = {"date": "the date of the letter", "client name": "the client's name", "firm": "the firm's name", "office": "the office's name",
                "office address": "the office's address", "attorney": "the attorney who signs", "filings": "the filings the letter is about",
                "fee": "the fee the office types", "government fees": "the government's fees, as the office types them",
                "matter": "what the person asked about (non-engagement)", "declined on": "the date the firm declined",
                "end date": "the date the representation ends", "ended by": "who ended it, in a sentence", "returned": "what the firm returns",
                "deadlines": "the deadlines the case knows of", "closed on": "the date the matter closed", "kept until": "how long the file is kept",
                "how to get the file": "how to get the file", "additions": "the attorney's own additions"}

# The firm practice the attorney approves (Keeping current, like a rule; src/rules/approval.py): the approval holds for the letters' wording as it
# stands (every office's copy of every letter, in every language): an edit to any of them shows "changed since approval" until approved again.
PRACTICE_ID = "PRACTICE:LETTERS"
PRACTICE = ("The firm's letters to clients about the engagement are made only from the firm's own wording on the Settings page (Firm documents, "
            "one copy per office): the engagement letter and fee agreement, the non-engagement letter, the disengagement letter and the closing "
            "letter. Each is filled in from the case (the client's name, the filings, the office, the dates, the deadlines the case knows of) "
            "and from what the office types (the fee, the government's fees, what the firm returns, the attorney's additions); the product never "
            "fills in a fee, a rule, a deadline or a keeping period of its own. Every letter is marked DRAFT until an attorney approves this "
            "wording, and an agreement is not sent to a client to sign before then. The Portuguese, Spanish and Haitian Creole versions are "
            "machine drafts until the firm's certified translator replaces them. Only an attorney declines, withdraws, transfers, closes or "
            "reopens a case, with a reason, and counter-signs an agreement.")
PRACTICE_SOURCE = ("The firm's own practice for its letters to clients, written for the attorney's approval. The rules of each state are cited "
                   "in docs/attorney_review.md only where they were read on the official page; the attorney reviews every letter against them")

# What each state's rules say, read on the official page with the date read (never from memory). retention_years is set only for a state whose
# rule on keeping a client's file was read on the official page and gives a period: none does today, so every office's keeping period is the
# firm's own setting (Settings, the office's section) and the product says so.
RULES: dict[str, dict[str, Any]] = {
    "FL": {"fees": "Rules Regulating The Florida Bar, Rule 4-1.5(e)(1) and (f)(1) to (2)", "ending": "Rule 4-1.16(d)",
           "source": "https://www.floridabar.org/rules/rrtfb/ (chapter 4, edition of October 1, 2026, "
                     "https://www-media.floridabar.org/uploads/2026/10/2027_04-OCT-Chapter-4-RRTFB.pdf, read 10/03/2026)",
           "retention_years": None, "retention_cite": None,
           "retention_note": "Chapters 4 and 5 of the Rules Regulating The Florida Bar (October 1, 2026, read 10/03/2026) set no period for keeping a "
                             "client's file: Rule 5-1.2 keeps trust account records 6 years, and Rule 4-1.5(f)(5) has each lawyer in a contingent fee "
                             "matter with a recovery keep the written fee contract and the closing statement for 6 years after the closing statement "
                             "is signed; neither is the client's file. The period is the firm's setting."},
    "MA": {"fees": None, "ending": None, "source": None, "retention_years": None, "retention_cite": None,
           "retention_note": "Massachusetts Rules of Professional Conduct 1.5 and 1.15A: mass.gov refused the request (HTTP 403) on 10/03/2026, so "
                             "neither is cited and no period is computed from them. The period is the firm's setting."},
}


# -- the firm's letters (Settings, Firm documents) ------------------------------------------------------------------------------


def firm_path() -> Path:
    """The firm's letters, beside its settings (data/firm_documents.json; I485_SETTINGS moves both, as the tests do)."""
    import settings

    return Path(settings.PATH).parent / FIRM_FILE


def shipped() -> dict[str, Any]:
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def _firm() -> dict[str, Any]:
    p = firm_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def stamp() -> tuple:
    """What the letters' wording depends on: the firm's letters and its offices (the approval's catalog reads it again when one changes)."""
    import settings

    p = firm_path()
    return (p.stat().st_mtime_ns if p.exists() else 0, settings.mtime(), hashlib.sha256(SHIPPED.read_bytes()).hexdigest())


def _office_ids() -> list[tuple[str, str]]:
    import offices

    return [(o["id"], o["name"]) for o in offices.offices()]


def document(office: str, kind: str) -> dict[str, Any]:
    """One office's copy of one letter: its texts by language (a list of paragraphs each), its version (0: as shipped), who edited it and when, and
    for each translation the version of the English it was made from (current: made from the English as it stands)."""
    if kind not in KINDS:
        raise LookupError("No such letter.")
    base = shipped()
    ship = base["documents"][kind]
    mine = ((_firm().get("offices") or {}).get(office) or {}).get(kind)
    if mine:
        out = {k: mine.get(k) for k in ("version", "english_version", "texts", "made_from", "made_from_english", "by", "at")}
    else:
        out = {"version": 0, "english_version": 0, "texts": ship["texts"], "made_from": {lg: 0 for lg in LANGS},
               "made_from_english": {lg: _english_hash(ship["texts"]["en"]) for lg in LANGS if lg != "en"}, "by": None, "at": None}
    out |= {"office": office, "kind": kind, "name": KINDS[kind], "title": ship["title"]}
    english = _english_hash(out["texts"]["en"])
    # a translation is current when it was made from the English as it stands now (its words, not the version number: the English edited and
    # put back as it was leaves the translation current)
    out["current"] = {lg: lg == "en" or (out["texts"].get(lg) is not None and (((out.get("made_from_english") or {}).get(lg) == english)
                                                                                 if (out.get("made_from_english") or {}).get(lg)
                                                                                 else (out["made_from"].get(lg) or 0) >= (out["english_version"] or 0)))
                      for lg in LANGS}
    out["wording_hash"] = wording_hash(out["texts"])
    out["machine"] = {lg: lg != "en" and out["texts"].get(lg) == ship["texts"].get(lg) for lg in LANGS}
    return out


def documents() -> list[dict[str, Any]]:
    """Settings, Firm documents: every office, each with its four letters."""
    return [{"id": oid, "name": name, "documents": [document(oid, kind) for kind in KINDS]} for oid, name in _office_ids()]


def _paragraphs(text: Any) -> list[str]:
    """A letter typed in a box: paragraphs separated by an empty line (a list is taken as it is)."""
    if isinstance(text, list):
        parts = [str(x) for x in text]
    else:
        parts = re.split(r"\n\s*\n", str(text or "").replace("\r\n", "\n"))
    return [" ".join(p.split()) for p in parts if p.strip()]


def _english_hash(paragraphs: list[str]) -> str:
    return hashlib.sha256(json.dumps(list(paragraphs or []), ensure_ascii=False).encode("utf-8")).hexdigest()


def wording_hash(texts: dict[str, list[str]]) -> str:
    """One office's copy of one letter, every language: what a letter made from it keeps, so it is sent only while that wording is the approved one."""
    return hashlib.sha256(json.dumps({lg: texts.get(lg) for lg in LANGS}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _brackets(paragraph: str) -> set[str]:
    return set(re.findall(r"\[([^\]]+)\]", paragraph))


def _same_words(english: list[str], other: list[str], lang: str) -> None:
    """A translation holds, paragraph by paragraph, the same words in square brackets as the English: a word left out would leave a fee, a date
    or a deadline out of the client's own letter."""
    name = LANGUAGE_NAMES[lang]
    if len(other) != len(english):
        raise ValueError(f"The {name} letter has {len(other)} paragraphs and the English {len(english)}: each paragraph of the English needs its "
                         f"{name} paragraph, in the same order.")
    for n, (en, tr) in enumerate(zip(english, other), 1):
        missing, extra = sorted(_brackets(en) - _brackets(tr)), sorted(_brackets(tr) - _brackets(en))
        if missing:
            raise ValueError(f"The {name} letter, paragraph {n}, leaves out " + ", ".join(f"[{w}]" for w in missing)
                             + ", which the English paragraph holds: put it back in the translation.")
        if extra:
            raise ValueError(f"The {name} letter, paragraph {n}, holds " + ", ".join(f"[{w}]" for w in extra)
                             + ", which the English paragraph does not: the translation fills in only what the English does.")


def _check_words(paragraphs: list[str], lang: str) -> None:
    unknown = sorted({m for p in paragraphs for m in re.findall(r"\[([^\]]+)\]", p)} - set(PLACEHOLDERS))
    if unknown:
        raise ValueError(f"The {LANGUAGE_NAMES[lang]} letter has words in square brackets the product cannot fill in: "
                         + ", ".join(f"[{u}]" for u in unknown) + ". It fills in: " + ", ".join(f"[{k}]" for k in PLACEHOLDERS) + ".")
    if len(" ".join(paragraphs)) > 20000:
        raise ValueError("That letter is too long (20,000 characters at most).")


@data_mutation(lambda: firm_path().parent)
def save_document(office: str, kind: str, texts: dict[str, Any], who: str, role: str | None = None) -> dict[str, Any]:
    """An attorney's edit of one office's copy of one letter: a new version, the earlier one kept with who and when. A translation whose words
    changed counts as made from this English; one left as it was while the English changed is no longer current (the portal then shows the
    English alone, and says so) until it is edited too."""
    if not str(who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    if role == "paralegal":
        raise PermissionError("Only an attorney changes the firm's letters.")
    if office not in {oid for oid, _ in _office_ids()}:
        raise LookupError("No such office.")
    now = document(office, kind)
    new = {lg: _paragraphs(texts[lg]) for lg in LANGS if lg in (texts or {}) and texts[lg] is not None}
    if "en" in new and not new["en"]:
        raise ValueError("The English letter can't be empty.")
    for lg, paras in new.items():
        _check_words(paras, lg)
    changed = {lg for lg, paras in new.items() if paras != (now["texts"].get(lg) or [])}  # the English sent back as it was is no change
    if not changed:
        return now
    english = new["en"] if "en" in changed else now["texts"]["en"]
    for lg in changed - {"en"}:
        _same_words(english, new[lg], lg)
    version = (now["version"] or 0) + 1
    english_version = version if "en" in changed else now["english_version"]
    made_from = dict(now["made_from"]) | {lg: version for lg in changed if lg != "en"}
    made_from_english = dict(now.get("made_from_english") or {}) | {lg: _english_hash(english) for lg in changed if lg != "en"}
    record = {"version": version, "english_version": english_version, "texts": dict(now["texts"]) | {lg: new[lg] for lg in changed},
              "made_from": made_from, "made_from_english": made_from_english, "by": who.strip(), "at": clock.stamp()}
    with _LOCK:
        data = _firm()
        data.setdefault("version", 1)
        slot = data.setdefault("offices", {}).setdefault(office, {})
        before = slot.get(kind)
        if before:
            record["history"] = (before.get("history") or []) + [{k: v for k, v in before.items() if k != "history"}]
        slot[kind] = record
        p = firm_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    name = dict(_office_ids()).get(office, "an office")
    events.record("settings", "changed", f"Changed the firm's {KINDS[kind].split(' (')[0].lower()} for {office_words(name)}: "
                  + ", ".join(LANGUAGE_NAMES[lg] for lg in LANGS if lg in changed), home=p.parent, who=who.strip(), role=role)
    return document(office, kind)


def wording() -> str:
    """Every office's copy of every letter, in every language: what the attorney's approval of the letters holds for."""
    return json.dumps([[o["id"], [[d["kind"], d["texts"]] for d in o["documents"]]] for o in documents()], ensure_ascii=False, sort_keys=True)


def practice_entry() -> dict[str, Any]:
    """The letters' practice in the approval catalog (src/rules/approval.py)."""
    text = {"plain_text": PRACTICE, "source": PRACTICE_SOURCE, "wording": hashlib.sha256(wording().encode("utf-8")).hexdigest()}
    return {"id": PRACTICE_ID, "kind": "practice", "code": PRACTICE_ID.split(":", 1)[1], "name": "Letters to clients: engagement, declining, withdrawing, closing",
            "plain_text": PRACTICE, "source": PRACTICE_SOURCE, "hash": hashlib.sha256(json.dumps(text, sort_keys=True).encode("utf-8")).hexdigest()}


def practice() -> dict[str, Any]:
    """The attorney's approval of the letters: {state, text, plain_text, id}."""
    from review.state import rule_info

    info = rule_info(PRACTICE_ID)
    return {"state": info["approval"]["state"], "text": info["approval_text"], "plain_text": info["plain_text"], "id": PRACTICE_ID}


def approved() -> bool:
    return practice()["state"] == "approved"


# -- the case's record ------------------------------------------------------------------------------------------------------------


def read(client_dir: Path) -> dict[str, Any]:
    p = Path(client_dir) / FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"version": VERSION, "letters": [], "end": None, "history": []} | data


def _save(client_dir: Path, rec: dict[str, Any], action: str, what: str, who: str | None = None, role: str | None = None) -> None:
    rec["version"] = VERSION
    p = Path(client_dir) / FILE
    previous = p.read_bytes() if p.exists() else None
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    row = events.record("engagement", action, what, case_dir=client_dir, who=who, role=role, version=VERSION)
    if action in ("file_prepared", "file_approved", "file_handed_over", "consent_approved") and not row:
        if previous is None:
            p.unlink(missing_ok=True)
        else:
            tmp.write_bytes(previous)
            os.replace(tmp, p)
        raise ValueError("The action could not be recorded in the event ledger; its case record was restored. Try again after the ledger is available.")


def state(client_dir: Path) -> str:
    end = read(client_dir).get("end")
    return end["state"] if end and end.get("state") in ENDED else "open"


def end_info(client_dir: Path) -> dict[str, Any] | None:
    """For a list (All clients, Search, My work): None for an open case (no file is read when there is no record), else the end state in words."""
    if not (Path(client_dir) / FILE).exists():
        return None
    end = read(client_dir).get("end")
    if not end or end.get("state") not in ENDED:
        return None
    return {"state": end["state"], "name": STATES[end["state"]], "since": end.get("on")}


def conflict_declined(client_dir: Path) -> dict[str, Any] | None:
    """A client the conflict search declined (src/conflicts.py), with no end recorded here: "Declined (conflict)" on All clients' ended filter,
    held from every message like any declined client, and offered the non-engagement letter on the Agreement and closing tab."""
    if not (Path(client_dir) / "conflict_check.json").exists():
        return None
    import conflicts

    found = conflicts.state(client_dir)
    if found.get("decision") != "declined":
        return None
    day = clock.local_date(found.get("at"))
    return {"state": "declined", "name": "Declined (conflict)", "since": day.isoformat() if day else None, "conflict": True}


def _need(who: str) -> str:
    who = str(who or "").strip()
    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    return who


def _attorney(role: str | None, what: str) -> None:
    if role == "paralegal":
        raise PermissionError(f"Only an attorney {what}.")


def _typed(value: Any, what: str, required: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ValueError(f"Type {what} first.")
    if len(text) > TEXT_MAX:
        raise ValueError(f"That is too long ({TEXT_MAX} characters at most).")
    return text


def _day(value: Any, what: str) -> date:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"Choose {what} from the calendar.")
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"Choose {what} from the calendar.") from None
    if day > clock.today():
        raise ValueError(f"{what[0].upper()}{what[1:]} can't be in the future.")
    return day


def _us(day: Any) -> str:
    d = clock.local_date(day) if not isinstance(day, date) else day
    return d.strftime("%m/%d/%Y") if d else ""


def _local(day: Any, lang: str) -> str:
    """12/31/2026 in English; 31/12/2026 in Portuguese, Spanish and Haitian Creole (as the portal writes dates, src/journey.py)."""
    d = clock.local_date(day) if not isinstance(day, date) else day
    return (d.strftime("%m/%d/%Y") if lang == "en" else d.strftime("%d/%m/%Y")) if d else ""


# -- what the letter is filled in from ------------------------------------------------------------------------------------------


def _portal_root(portal_root: Path | None) -> Path:
    return Path(portal_root or os.environ.get("PORTAL_DATA") or REPO / "data" / "portal")


def _profile(client_dir: Path, portal_root: Path | None) -> dict[str, Any]:
    p = _portal_root(portal_root) / "clients" / Path(client_dir).name / "profile.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def client_name(client_dir: Path, portal_root: Path | None = None) -> str:
    """The client's name as the portal holds it (as they or the office typed it), else from the case's facts, in ordinary capitals."""
    name = str(_profile(client_dir, portal_root).get("name") or "").strip()
    if name:
        return name
    try:
        from factgraph import FactGraph
        from review.state import case_summary

        graph = Path(client_dir) / "fact_graph.json"
        name = (case_summary(FactGraph.load(graph)).get("name") if graph.exists() else None) or ""
    except Exception:  # noqa: BLE001 -- a letter still names "the client"
        name = ""
    return " ".join(w.capitalize() for w in str(name).split()) or "the client"


def language(client_dir: Path, portal_root: Path | None = None) -> str:
    """The language the client reads the portal in (English for a client not in the portal)."""
    lang = str(_profile(client_dir, portal_root).get("language") or "en")
    return lang if lang in LANGS else "en"


def _office(client_dir: Path) -> dict[str, Any]:
    """The case's office, its letterhead and signer (src/offices.py letter: the firm's own once it has configured the product)."""
    import offices
    import settings

    office = offices.for_case(client_dir)
    config = json.loads((schema_path.path("cover_letter", "i485")).read_text(encoding="utf-8"))
    letter = offices.letter(config, client_dir)
    head = letter.get("letterhead") or {}
    v = office["values"]
    firm = (settings.firm_name() or str(v.get("firm.business_name") or "") or " ".join(x for x in (head.get("name_light"), head.get("name_bold")) if x)).strip()
    street = ", ".join(x for x in (v.get("firm.street"), v.get("firm.city"), v.get("firm.state")) if x)
    address = f"{street} {v.get('firm.zip') or ''}".strip() if street else str(head.get("address") or "").title()
    signer = letter.get("signer") or {}
    return {"id": office["id"], "name": office["name"], "state": str(v.get("firm.state") or "").upper(), "firm": firm or "our firm",
            "address": address, "attorney": str(signer.get("name") or "").strip(),
            "letterhead": {"name": firm.upper(), "tagline": str(head.get("tagline") or ""), "address": str(head.get("address") or address.upper()),
                           "attorneys": str(head.get("attorneys") or "")},
            "signer": {"name": str(signer.get("name") or ""), "lines": [str(x) for x in signer.get("lines") or []]}}


def filing_choices() -> list[list[str]]:
    """The filings a letter can name: every filing the product prepares, in a person's words."""
    import packet

    return [[f, packet.filing_title(f)] for f in packet.FILINGS]


# The forms of a filing the portal's own words do not name (src/journey.py _FILING_FORMS), as printed on the form; and the filings that are no form at
# all, named in the client's language by a sentence of their own (schemas/firm/firm_documents.json phrases). Nothing else: a filing id is never written.
FORMS = {"address": ["AR-11"], "i912": ["I-912"], "i914b": ["I-914 Supplement B"], "g639": ["G-639"]}
SPOKEN = {"expedite": "filing_expedite", "court_bond": "filing_court_bond", "court_motion": "filing_court_motion"}


def office_words(name: str) -> str:
    """"the Chelsea, MA office", and "the Main office" (never "the Main office office")."""
    return f"the {name}" if str(name).lower().endswith("office") else f"the {name} office"


def _filing_words(filings: list[str], lang: str) -> str:
    """The filings in the letter's language: their names in English (packet.filing_title); in the others, the form numbers with the language's
    own word for a form (the portal's words, src/journey.py) and its own word for a supplement, and a sentence for a filing that is no form."""
    import journey
    import packet

    if lang == "en":
        names = [packet.filing_title(f) for f in filings]
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""
    words = journey.settings().get("client_events") or {}
    phrases = shipped()["phrases"]
    supplement = phrases["supplement"].get(lang) or phrases["supplement"]["en"]
    forms, spoken = [], []
    for f in filings:
        if f in SPOKEN:
            spoken.append(phrases[SPOKEN[f]].get(lang) or phrases[SPOKEN[f]]["en"])
        elif f in FORMS or f in journey._FILING_FORMS:
            forms += FORMS.get(f) or journey._forms_of({"filing": f})
        else:  # a filing added later and not named for the client yet: its name as the office reads it, never its id
            spoken.append(packet.filing_title(f))
    forms = [x.replace(" Supplement ", f" {supplement} ") for x in dict.fromkeys(forms)]
    parts = ([journey._form_names(forms, words, lang)] if forms else []) + spoken
    joiner = journey._say(words["_and"], lang) if words.get("_and") else "and"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" {joiner} " + parts[-1] if parts else ""


def known_deadlines(client_dir: Path) -> list[dict[str, str]]:
    """The deadlines the case knows of (src/journey.py), for the disengagement letter: date and what, as the timeline says them, and those already
    past marked so (a late answer may still matter: the attorney decides). When deadlines a person sets arrive (brief I2), they stay out of a
    client's letter: only the product's own deadlines are copied (docs/decisions.md, I1 after verification)."""
    try:
        import journey

        today = clock.today().isoformat()
        return [{"date": d["date"], "what": d["what"], "passed": d["date"] < today} for d in journey.journey(Path(client_dir)).get("deadlines") or []
                if d.get("date") and not d.get("set")]  # "set": a deadline a person typed (src/deadlines_set.py): the office's own note, never the client's letter
    except Exception:  # noqa: BLE001 -- a case whose timeline can't be read says it knows of none, and the letter says that does not mean there is none
        return []


def packet_fees(client_dir: Path) -> list[dict[str, str]]:
    """What each built packet's cover letter states as the fee (the packet's record, from the government's fee schedule): shown to the office
    beside the government fees box, never put in a letter by itself."""
    import packet

    out = []
    for filing, name in packet.FILINGS.items():
        p = Path(client_dir) / name
        try:
            fee = (json.loads(p.read_text(encoding="utf-8")).get("fee") or "").strip() if p.exists() else ""
        except (OSError, ValueError):
            fee = ""
        if fee:
            out.append({"filing": packet.filing_title(filing), "fee": fee})
    return out


def _fill(text: str, values: dict[str, str]) -> str:
    return re.sub(r"\[([^\]]+)\]", lambda m: values.get(m.group(1), m.group(0)), text)


def _compose(kind: str, office_doc: dict[str, Any], lang: str, values: dict[str, str]) -> list[str]:
    """The letter's paragraphs in one language, every word in brackets filled in; a paragraph left empty is left out."""
    return [filled for filled in (" ".join(_fill(p, values).split()) for p in office_doc["texts"].get(lang) or []) if filled]


def _values(kind: str, lang: str, ctx: dict[str, Any]) -> dict[str, str]:
    """The words in brackets, in one language: the case's, the office's typed parts, and the product's sentences (schemas/firm/firm_documents.json
    phrases, DRAFT like the letters)."""
    phrases = shipped()["phrases"]
    say = lambda key: phrases[key].get(lang) or phrases[key]["en"]  # noqa: E731
    office = ctx["office"]
    v = {"date": _local(ctx["date"], lang), "client name": ctx["client"], "firm": office["firm"], "office": office["name"],
         "office address": office["address"] or office["name"], "attorney": office["attorney"] or office["firm"],
         "filings": _filing_words(ctx.get("filings") or [], lang) or say("your_matter"),
         "fee": ctx.get("fee") or "", "government fees": ctx.get("government_fees") or say("government_fees"),
         "matter": ctx.get("matter") or say("your_matter"), "additions": ctx.get("additions") or "",
         "returned": ctx.get("returned") or say("returned")}
    if ctx.get("on"):
        for key in ("declined on", "end date", "closed on"):
            v[key] = _local(ctx["on"], lang)
    if kind == "disengagement":
        v["ended by"] = say("ended_transferred" if ctx.get("state") == "transferred" else "ended_by_client" if ctx.get("ended_by") == "client" else "ended_by_firm")
        found = ctx.get("deadlines") or []
        listed = "; ".join(f"{_local(d['date'], lang)}" + (f" ({say('already_passed')})" if d.get("passed") else "") + f", {d['what']}" for d in found)
        v["deadlines"] = (say("deadlines_some").format(list=listed) + (f" ({say('deadlines_in_english')})" if lang != "en" else "") if found
                          else say("deadlines_none"))
    if kind == "closing":
        until = (ctx.get("retention") or {}).get("until")
        v["kept until"] = say("kept_until_date").format(date=_local(until, lang)) if until and ctx["retention"].get("approved") else say("kept_until_proposal")
        v["how to get the file"] = ctx.get("how") or _fill(say("how_to_request_file"), v)
    return v


def _letter(client_dir: Path, rec: dict[str, Any], kind: str, ctx: dict[str, Any], who: str, role: str | None) -> dict[str, Any]:
    """Makes a letter from the office's copy of its wording, in English and in the client's language (when that translation is current), and
    draws its PDF. The record keeps everything it was made from, so the PDF can be drawn again as it was."""
    office = ctx["office"]
    doc = document(office["id"], kind)
    lang = ctx["language"]
    texts = {"en": _compose(kind, doc, "en", _values(kind, "en", ctx))}
    if lang != "en" and doc["current"].get(lang):
        texts[lang] = _compose(kind, doc, lang, _values(kind, lang, ctx))
    n = 1 + max((int(x["id"][1:]) for x in rec["letters"] if re.fullmatch(r"L\d+", x.get("id") or "")), default=0)
    letter = {"id": f"L{n}", "kind": kind, "office": office["id"], "office_name": office["name"], "template_version": doc["version"],
              "english_version": doc["english_version"], "language": lang, "translation": ("machine" if doc["machine"].get(lang) else "firm") if lang in texts and lang != "en" else None,
              "titles": {lg: doc["title"].get(lg) or doc["title"]["en"] for lg in texts}, "texts": texts, "client": ctx["client"],
              "letterhead": office["letterhead"], "signer": office["signer"], "date": ctx["date"].isoformat(),
              "filings": ctx.get("filings") or [], "fee": ctx.get("fee") or None, "government_fees": ctx.get("government_fees") or None,
              "additions": ctx.get("additions") or None, "made_by": who, "made_role": role, "made_at": clock.stamp(), "approved": approved(),
              "wording_hash": doc["wording_hash"]}
    rec["letters"].append(letter)
    return letter


APPROVE_FIRST = "The attorney approves the letters' wording first (Keeping current, or the button on this panel): until then every letter is a DRAFT, and none goes to the client."


def _approved_wording(letter: dict[str, Any]) -> None:
    """A letter goes to the client only while the attorney's approval holds and the wording it was made from is the wording approved: a letter made
    from wording that changed since (an edit, approved or not) is made again from the wording as it stands."""
    if not approved():
        raise ValueError(APPROVE_FIRST)
    try:
        now = document(letter["office"], letter["kind"])["wording_hash"]
    except LookupError:
        now = None
    if not letter.get("wording_hash") or letter["wording_hash"] != now:
        raise ValueError("The wording changed since this letter was made: make it again.")


def _context(client_dir: Path, portal_root: Path | None, **extra: Any) -> dict[str, Any]:
    return {"office": _office(client_dir), "client": client_name(client_dir, portal_root), "language": language(client_dir, portal_root),
            "date": clock.today()} | extra


def _find(rec: dict[str, Any], letter_id: str) -> dict[str, Any]:
    letter = next((x for x in rec["letters"] if x.get("id") == letter_id), None)
    if letter is None:
        raise LookupError("No such letter on this case.")
    return letter


def _open_only(rec: dict[str, Any]) -> None:
    end = rec.get("end")
    if end and end.get("state") in ENDED:
        raise ValueError(f"This case is {STATES[end['state']].lower()} since {_us(end.get('on'))}: an attorney reopens it first.")


# -- the agreement ------------------------------------------------------------------------------------------------------------------


def _agreement_context(client_dir, filings, fee, government_fees, additions, portal_root):
    """One read-only input/context contract shared by preview and creation."""
    import packet
    client_dir = Path(client_dir)
    if not client_file.directory_safe(client_dir):
        raise ValueError("Case folder cannot use a link or reparse point.")
    client_file.allowed(client_dir)
    rec = read(client_dir)
    _open_only(rec)
    chosen = [f for f in dict.fromkeys(filings or []) if f in packet.FILINGS]
    if not chosen:
        raise ValueError("Choose at least one filing the agreement covers.")
    fee = _typed(fee, "the fee, as the firm charges it (the product never fills one in)", required=True)
    ctx = _context(client_dir, portal_root, filings=chosen, fee=fee, government_fees=_typed(government_fees, "the government fees"),
                   additions=_typed(additions, "the additions"))
    return rec, ctx


def preview_agreement(client_dir: Path, filings: list[str], fee: str, government_fees: str = "", additions: str = "",
                      portal_root: Path | None = None) -> dict[str, Any]:
    """Exact current paragraphs without a letter, PDF, signature, audit or send.

    The protected caller authorizes the current case. Preview is not a saved
    letter or permission to send. Creation rechecks current inputs and wording.
    """
    rec, ctx = _agreement_context(client_dir, filings, fee, government_fees, additions, portal_root)
    letter = _letter(Path(client_dir), rec, "engagement", ctx, "Preview", None)
    fields = ("kind", "office", "office_name", "template_version", "english_version", "language", "translation", "titles", "texts",
              "client", "letterhead", "signer", "date", "filings", "fee", "government_fees", "additions", "wording_hash")
    return {**{key: letter[key] for key in fields}, "preview": True, "draft": not letter["approved"],
            "draft_reason": APPROVE_FIRST if not letter["approved"] else None,
            "translation_available": ctx["language"] in letter["texts"]}


def make_agreement(client_dir: Path, filings: list[str], fee: str, who: str, role: str | None = None, government_fees: str = "",
                   additions: str = "", portal_root: Path | None = None) -> dict[str, Any]:
    """Make the agreement using the same inputs and wording as its pure preview."""
    who = _need(who)
    rec, ctx = _agreement_context(client_dir, filings, fee, government_fees, additions, portal_root)
    letter = _letter(client_dir, rec, "engagement", ctx, who, role)
    _save(client_dir, rec, "made", "Made the engagement letter and fee agreement" + (" (a draft: the letters' wording is not approved yet)" if not letter["approved"] else ""), who, role)
    render(client_dir, letter)
    return view(client_dir, portal_root, role)


def agreement(rec: dict[str, Any]) -> dict[str, Any] | None:
    """The case's agreement: the latest engagement letter."""
    return next((x for x in reversed(rec["letters"]) if x["kind"] == "engagement"), None)


def send(client_dir: Path, letter_id: str, who: str, role: str | None = None, portal_root: Path | None = None) -> dict[str, Any]:
    """Puts the agreement on the client's portal to read and sign, in their language with the English beside it. Refused before the attorney
    approves the letters' wording, and for a client not in the portal (the office then has it signed on paper)."""
    from portal.store import PortalStore

    who = _need(who)
    _attorney(role, "sends the firm's agreement to the client (the office prepares it; the attorney puts it in front of the client)")
    rec = read(client_dir)
    _open_only(rec)
    letter = _find(rec, letter_id)
    if letter["kind"] != "engagement" or letter is not agreement(rec):
        raise ValueError("Only the latest agreement is sent to be signed.")
    if letter.get("signature"):
        raise ValueError("The client has signed this agreement already.")
    _approved_wording(letter)
    root = _portal_root(portal_root)
    if not (root / "clients" / Path(client_dir).name / "profile.json").exists():
        raise LookupError("This client is not in the portal: print the agreement and record the paper signature instead.")
    letter["approved"] = True
    consent = rec.get("electronic_consent") or {}
    shown_language = letter["language"] if letter["language"] in letter["texts"] else "en"
    letter["consent"] = consent if consent.get("language") == shown_language else None
    store = PortalStore(root)
    cid = Path(client_dir).name
    store.save_engagement(cid, {"letter": _portal_letter(letter), "signed": None, "file_sent": bool((rec.get("file") or {}).get("returned"))})
    store.log(cid, "agreement_sent", {"by": who})
    letter["sent"] = {"by": who, "at": clock.stamp(), "delivery": _tell_client(store, cid, Path(client_dir).parent)}
    _save(client_dir, rec, "sent", "Sent the agreement to the client's portal to sign", who, role)
    render(client_dir, letter)
    return view(client_dir, portal_root, role)


def _tell_client(store, client_id: str, cases_root: Path) -> str:
    """The portal's "there's news, sign in" message (no case details in it) on the channels the client agreed to, as a mailing recorded does
    (src/journey.py push_client): nothing for a restricted case, a held one or an ended one (portal/notify.py allowed), and no sign-in link is
    made then. Returns what was done, in words."""
    from portal.notify import Notifier, delivery

    notifier = Notifier(store.root / "outbox.jsonl", cases_root=cases_root, store=store)
    profile = store.profile(client_id)
    said = delivery(notifier.send(profile, "case_update"))
    store.log(client_id, "case_update", {"stage": "agreement", "message": said["text"]})
    return said["text"]


def _portal_letter(letter: dict[str, Any]) -> dict[str, Any]:
    """What the portal shows: the letter's title and paragraphs in English and in the client's language (when current), nothing else."""
    return {"id": letter["id"], "kind": letter["kind"], "titles": letter["titles"], "texts": letter["texts"], "language": letter["language"],
            "date": letter["date"], "translation": letter.get("translation"), "consent": letter.get("consent")}


def sync(client_dir: Path, portal_root: Path | None = None) -> bool:
    """The client's signature from the portal onto the case: the signed copy drawn with the typed name, the date, the time, the address the
    request came from and the language shown. Called by the review app when the case is opened and by the portal when the client signs.
    True when something new was recorded. Never raises."""
    try:
        p = _portal_root(portal_root) / "clients" / Path(client_dir).name / "engagement.json"
        if not p.exists() or not (Path(client_dir) / FILE).exists():
            return False
        portal_record = json.loads(p.read_text(encoding="utf-8")) or {}
        signed = portal_record.get("signed")
        if not signed:
            return False
        with _LOCK:
            rec = read(client_dir)
            letter = next((x for x in rec["letters"] if x.get("id") == signed.get("letter")), None)
            if letter is None or letter.get("signature"):
                return False
            letter["signature"] = {"how": "portal", "typed_name": signed.get("typed_name"), "at": signed.get("at"), "address": signed.get("address"),
                                   "language": signed.get("language"), "consent": signed.get("consent"),
                                   "authenticated_client": Path(client_dir).name}
            import signing_evidence

            shown = signed.get("letter_snapshot")
            if shown and shown.get("id") == letter["id"]:
                letter["signing_evidence"] = signing_evidence.retain(client_dir, letter, shown, letter["signature"])
            _save(client_dir, rec, "signed", "The client signed the agreement in the portal", who="The client", role="client")
        render(client_dir, letter)
        return True
    except Exception as exc:  # noqa: BLE001 -- the portal keeps the signature; the next opening of the case records it
        sys.stderr.write(f"agreement signature not recorded on the case ({type(exc).__name__})\n")
        return False


def paper(client_dir: Path, letter_id: str, data: bytes, on: str, who: str, role: str | None = None, portal_root: Path | None = None) -> dict[str, Any]:
    """The client signed on paper: the office uploads the scan (PDF, JPEG or PNG; a photo is kept as a PDF) and the date it was signed."""
    from portal.store import MAX_UPLOAD, file_kind, image_to_pdf

    who = _need(who)
    rec = read(client_dir)
    _open_only(rec)
    letter = _find(rec, letter_id)
    if letter["kind"] != "engagement":
        raise ValueError("Only an agreement is signed.")
    if letter.get("signature"):
        raise ValueError("The client has signed this agreement already.")
    _approved_wording(letter)
    day = _day(on, "the date the client signed")
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("Add the scan of the signed letter (15 MB at most).")
    kind = file_kind(data)
    if kind is None:
        raise ValueError("The scan must be a PDF, JPG or PNG.")
    pdf = data if kind == "application/pdf" else image_to_pdf(data)
    folder = Path(client_dir) / FOLDER
    folder.mkdir(exist_ok=True)
    name = f"agreement-{letter['id']}-signed-on-paper.pdf"
    (folder / name).write_bytes(pdf)
    letter["approved"] = True
    letter["signature"] = {"how": "paper", "on": day.isoformat(), "file": name, "sha256": hashlib.sha256(pdf).hexdigest(), "by": who, "at": clock.stamp()}
    _save(client_dir, rec, "signed", "Recorded the client's signature on the paper agreement", who, role)
    render(client_dir, letter)
    _portal_signed_on_paper(client_dir, letter, portal_root)
    return view(client_dir, portal_root, role)


def _portal_signed_on_paper(client_dir: Path, letter: dict[str, Any], portal_root: Path | None) -> None:
    """The portal's card says the agreement is signed (and stops asking) when the client signed on paper."""
    from portal.store import PortalStore

    root = _portal_root(portal_root)
    if (root / "clients" / Path(client_dir).name / "profile.json").exists():
        store = PortalStore(root)
        store.save_engagement(Path(client_dir).name, {"letter": _portal_letter(letter), "signed": {"letter": letter["id"], "how": "paper",
                                                                                                  "at": letter["signature"]["on"]}})


def countersign(client_dir: Path, letter_id: str, typed_name: str, who: str, role: str | None = None, portal_root: Path | None = None) -> dict[str, Any]:
    """The attorney counter-signs the signed agreement: their typed name, on the same record and the same PDF."""
    who = _need(who)
    _attorney(role, "counter-signs an agreement")
    rec = read(client_dir)
    letter = _find(rec, letter_id)
    if letter["kind"] != "engagement" or not letter.get("signature"):
        raise ValueError("The client signs the agreement first.")
    if letter.get("countersignature"):
        raise ValueError("The agreement is counter-signed already.")
    typed = _typed(typed_name, "your full name, as you sign", required=True)
    if len(typed) < 3:
        raise ValueError("Type your full name, as you sign.")
    if len(typed) > 120:
        raise ValueError("That name is too long (120 characters at most): type it as you sign.")
    letter["countersignature"] = {"typed_name": typed, "by": who, "role": role, "at": clock.stamp()}
    _save(client_dir, rec, "countersigned", "Counter-signed the agreement for the firm", who, role)
    render(client_dir, letter)
    return view(client_dir, portal_root, role)


# -- the end of the case ----------------------------------------------------------------------------------------------------------


def _years(value: Any) -> int | None:
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() and 1 <= int(text) <= 50 else None


def _plus_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # the 29th of February in a year that has none
        return day.replace(year=day.year + years, day=28)


def retention(client_dir: Path, ended_on: date) -> dict[str, Any]:
    proposal = _retention_proposal(client_dir, ended_on)
    try:
        current = client_file_policy.view(client_dir)
        if current["retention"]["state"] == "approved":
            approved = current["retention"]["approval"]
            return proposal | {"until": approved["keep_until"], "basis": "case_policy", "approved": True,
                               "planning_until": proposal["until"],
                               "words": f"Kept through {_us(date.fromisoformat(approved['keep_until']))}: the current attorney-approved case policy. Destruction requires separate approval."}
    except (ValueError, OSError, PermissionError, TypeError, KeyError):
        pass
    return proposal | {"approved": False, "words": "Planning proposal only: " + proposal["words"].replace("Kept until", "Proposed date") +
                       " Operational closure and an office period do not establish legal termination or destruction authority."}


def _retention_proposal(client_dir: Path, ended_on: date) -> dict[str, Any]:
    """The keeping date of a case that ended on this day: from the office's state's rule only where that rule was read on the official page and
    gives a period (RULES), else the period the attorney set for the office in Settings, else none (and the screen says to set it). The words say
    which: a rule is cited, a setting is called the firm's setting."""
    office = _office(client_dir)
    rule = RULES.get(office["state"]) or {}
    import offices

    years, basis, cite = rule.get("retention_years"), None, None
    confirmed = None
    try:  # the office's rule as the attorney confirmed it (Settings, Keeping closed files: src/purge.py), counted from majority when it says so
        import purge

        found = purge.rule_for(Path(client_dir).parent, offices.by_id(office["id"]) or {"id": office["id"], "name": office["name"], "values": {}})
        confirmed = found if found["valid"] else None
    except Exception:  # noqa: BLE001 -- the office's own years below
        confirmed = None
    if confirmed:
        starts = ended_on
        born = purge.birth_date(Path(client_dir)) if confirmed["from_majority"] else None
        if born:
            starts = max(ended_on, _plus_years(born, purge.MAJORITY))
        until = _plus_years(starts, confirmed["years"])
        return {"until": until.isoformat(), "years": confirmed["years"], "basis": "confirmed", "cite": None, "office": office["name"],
                "words": f"Kept until {_us(until)}: the office's retention rule, as the attorney set it on {_us(confirmed['confirmed']['at'])}."}
    if years:
        basis, cite = "rule", rule.get("retention_cite")
    else:
        years = _years((offices.by_id(office["id"]) or {}).get("values", {}).get("office.retention_years"))
        basis = "setting" if years else None
    until = _plus_years(ended_on, years) if years else None
    if basis == "rule":
        words = f"Kept until {_us(until)}: {years} years after the case ended, under {cite}."
    elif basis == "setting":
        words = f"Kept until {_us(until)}: {years} years after the case ended, the firm's setting for {office_words(office['name'])}."
    else:
        words = (f"No keeping date yet: the firm has not set how long it keeps a closed file for {office_words(office['name'])} "
                 "(Settings, the office's section). Set it and the date follows.")
    return {"until": until.isoformat() if until else None, "years": years, "basis": basis, "cite": cite, "office": office["name"], "words": words}


def end(client_dir: Path, new_state: str, who: str, role: str | None = None, reason: str = "", additions: str = "", matter: str = "",
        returned: str = "", how: str = "", ended_by: str = "", portal_root: Path | None = None, store=None) -> dict[str, Any]:
    """The attorney declines, withdraws, transfers or closes the case, with a reason (kept for the firm; the client never sees it): its letter is
    made from the office's wording with the attorney's typed additions, the keeping date is worked out, and the portal changes (a declined
    person's link stops working today; an ended client sees the letter and nothing else)."""
    who = _need(who)
    _attorney(role, "declines, withdraws, transfers or closes a case")
    if new_state not in ENDED:
        raise ValueError("Choose what happens to the case: declined, withdrawn, transferred or closed.")
    reason = _typed(reason, "the reason (kept for the firm; the client never sees it)", required=True)
    if not approved():
        raise ValueError(APPROVE_FIRST)
    sync(client_dir, portal_root)  # a signature the client made in the portal that the case has not recorded yet
    rec = read(client_dir)
    _open_only(rec)
    signed = agreement(rec) and (agreement(rec).get("signature") or _portal_signed(client_dir, agreement(rec), portal_root))
    if new_state == "declined" and signed:
        raise ValueError("The client signed an agreement: the firm took the case. Withdraw or close it instead.")
    if new_state == "withdrawn" and ended_by not in ("firm", "client"):
        raise ValueError("Say who ended the representation: the firm, or the client.")
    today = clock.today()
    kind = LETTER_FOR[new_state]
    filings = (agreement(rec) or {}).get("filings") or []
    keep = retention(client_dir, today)
    ctx = _context(client_dir, portal_root, filings=filings, on=today, state=new_state, ended_by=ended_by or ("client" if new_state == "transferred" else "firm"),
                   additions=_typed(additions, "the additions"), matter=_typed(matter, "what the person asked about"),
                   returned=_typed(returned, "what the firm returns"), how=_typed(how, "how to get the file"),
                   deadlines=known_deadlines(client_dir) if kind == "disengagement" else [], retention=keep)
    letter = _letter(client_dir, rec, kind, ctx, who, role)
    letter |= {k: ctx[k] or None for k in ("matter", "returned", "how") if k in ctx} | ({"deadlines": ctx["deadlines"]} if kind == "disengagement" else {})
    # the end date only: the keeping date is worked out from it and the office's setting every time it is read, so a period set later counts
    rec["end"] = {"state": new_state, "on": today.isoformat(), "reason": reason, "ended_by": ctx["ended_by"] if new_state in ("withdrawn", "transferred") else None,
                  "by": who, "role": role, "at": clock.stamp(), "letter": letter["id"]}
    _save(client_dir, rec, new_state, f"{STATE_WORDS[new_state]}: the case is {STATES[new_state].lower()}, and the {KINDS[kind].split(' (')[0].lower()} was made", who, role)
    render(client_dir, letter)
    _portal_end(client_dir, rec, letter, portal_root, store)
    return view(client_dir, portal_root, role)


def _portal_signed(client_dir: Path, letter: dict[str, Any] | None, portal_root: Path | None) -> bool:
    """The client signed this agreement in the portal (whether or not the case has recorded it yet)."""
    if not letter:
        return False
    try:
        p = _portal_root(portal_root) / "clients" / Path(client_dir).name / "engagement.json"
        signed = (json.loads(p.read_text(encoding="utf-8")) or {}).get("signed") if p.exists() else None
    except (OSError, ValueError):
        return False
    return bool(signed and signed.get("letter") == letter.get("id"))


def _portal_end(client_dir: Path, rec: dict[str, Any], letter: dict[str, Any] | None, portal_root: Path | None, store=None) -> None:
    """The portal for an ended case: a declined person's link stops working from today; anyone else sees "closed since" and the letter."""
    from portal.store import PortalStore

    root = _portal_root(portal_root)
    cid = Path(client_dir).name
    if not (root / "clients" / cid / "profile.json").exists():
        return
    store = store or PortalStore(root)  # store: the prospects' own (src/prospects.py), whose ledger rows say prospect
    end_ = rec.get("end")
    current = store.engagement(cid)
    if end_ and end_.get("state") in ENDED:
        if end_["state"] == "declined":
            store.update_profile(cid, declined_on=end_["on"])
            store.end_sessions(cid)
        shown = _portal_letter(letter) if letter and letter.get("approved") else None  # never a DRAFT letter on the client's page
        store.save_engagement(cid, current | {"ended": {"state": end_["state"], "since": end_["on"], "letter": shown}})
        store.log(cid, "case_ended", {"state": end_["state"]})
    else:
        profile = store.profile(cid)
        if profile.get("declined_on"):
            store.update_profile(cid, declined_on=None)
        store.save_engagement(cid, {k: v for k, v in current.items() if k != "ended"})
        store.log(cid, "case_reopened", {})


def reopen(client_dir: Path, reason: str, who: str, role: str | None = None, portal_root: Path | None = None) -> dict[str, Any]:
    """The attorney opens an ended case again, with a reason: the end is kept in the case's history, the case is back on the work lists, the
    portal works again."""
    who = _need(who)
    _attorney(role, "reopens a case")
    reason = _typed(reason, "why the case is opened again", required=True)
    rec = read(client_dir)
    if not rec.get("end"):
        raise ValueError("The case is open.")
    rec["history"].append(rec["end"] | {"reopened": {"by": who, "role": role, "at": clock.stamp(), "reason": reason}})
    rec["end"] = None
    _save(client_dir, rec, "reopened", "Opened the case again", who, role)
    _portal_end(client_dir, rec, None, portal_root)
    return view(client_dir, portal_root, role)


# -- the file returned to the client ------------------------------------------------------------------------------------------------


EXPORT_NAME = re.compile(r"i485-case-file-\d{4}-\d{2}-\d{2}-\d+\.zip")


def exports_folder(data_root: Path) -> Path:
    return Path(data_root).parent / "exports"


@client_file.serialized
def export_file(client_dir: Path, data_root: Path, who: str, role: str | None = None, portal_root: Path | None = None,
                expected_binding_sha256: str = "") -> dict[str, Any]:
    """Prepare client documents, filed copies and released letters for explicit attorney review."""
    import client_file

    who = _need(who)
    _attorney(role, "prepares the client's file")
    client_file.allowed(client_dir)
    client_file_policy.require_handover(client_dir, portal_root)
    sync(client_dir, portal_root)
    binding = client_file_policy.handover_binding(client_dir, portal_root)
    if expected_binding_sha256 != client_file_policy.digest(binding):
        raise ValueError("Review the current jurisdiction, inventory and recipient before preparing this file.")
    sys.path.append(str(REPO / "tools"))
    import export_firm

    entries, excluded, warnings = client_file.reviewed_entries(Path(client_dir), _portal_root(portal_root),
        client_file_policy.read(client_dir, portal_root)["inventory_approval"])
    folder = exports_folder(data_root)
    if not client_file.directory_safe(folder):
        raise ValueError("Client exports folder cannot use a link or reparse point.")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    rec = read(client_dir)
    prior = (rec.get("file") or {}).get("name") or ""
    if prior and not EXPORT_NAME.fullmatch(prior):
        raise ValueError("Invalid client-file record.")
    if not prior:
        day, n = clock.today().isoformat(), 1
        while True:
            prior = f"i485-case-file-{day}-{n}.zip"
            try:
                os.close(os.open(folder / prior, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
                break
            except FileExistsError:
                n += 1
    target = folder / prior
    if not client_file.directory_safe(target):
        raise ValueError("Client-file archive cannot use a link or reparse point.")
    count, sha = export_firm.write_zip(entries, target, "Client file selected for attorney review", [],
                                     ["Contact the office about missing documents."] if warnings else [],
                                     not_included=["Internal authority/security records and materials explicitly excluded in the attorney's current inventory review. Contact the office about missing material."])
    target.chmod(0o600)
    included = client_file.inspect(target, sha)
    old = rec.get("file")
    if old:
        rec.setdefault("file_history", []).append({k: old.get(k) for k in
            ("sha256", "by", "at", "files", "approved", "returned", "language", "binding", "binding_sha256", "policy")})
    rec["file"] = {"name": target.name, "files": count, "bytes": target.stat().st_size, "sha256": sha,
                   "by": who, "at": clock.stamp(), "included": included, "excluded": excluded, "warnings": warnings,
                   "language": language(client_dir, portal_root), "cover_draft": True, "approved": None, "returned": None,
                   "binding": binding, "binding_sha256": client_file_policy.digest(binding),
                   "policy": client_file_policy.read(client_dir, portal_root)["applicability_approval"]}
    try:
        _save(client_dir, rec, "file_prepared", f"Prepared the client's file for attorney review ({count} files); SHA256 {sha}", who, role)
    except Exception:
        if not (read(client_dir).get("file") or {}).get("name"):
            target.unlink(missing_ok=True)
        raise
    return view(client_dir, portal_root, role)


@client_file.serialized
def approve_file(client_dir: Path, sha256: str, who: str, role: str | None = None, portal_root: Path | None = None,
                 expected_binding_sha256: str = "") -> dict:
    import client_file

    who = _need(who)
    _attorney(role, "approves the client's file")
    client_file.allowed(client_dir)
    rec = read(client_dir)
    f = rec.get("file") or {}
    if not sha256 or f.get("sha256") != sha256:
        raise ValueError("Review this prepared file and its fingerprint before approval.")
    if expected_binding_sha256 != f.get("binding_sha256"):
        raise ValueError("Review this prepared file's current policy, inventory and recipient binding before approval.")
    if f.get("warnings"):
        raise ValueError("Resolve every missing-file or evidence warning before approval.")
    file_path(client_dir, Path(client_dir).parent, portal_root)
    f["approved"] = {"by": who, "role": role, "at": clock.stamp(), "sha256": sha256,
                     "binding_sha256": f["binding_sha256"],
                     "reviewed": "Current jurisdiction policy, included/excluded inventory, client cover and recipient authority/method"}
    _save(client_dir, rec, "file_approved", f"Approved the client's prepared file for handover; SHA256 {sha256}", who, role)
    return view(client_dir, portal_root, role)


@client_file.serialized
def file_returned(client_dir: Path, on: str, how: str, who: str, role: str | None = None, portal_root: Path | None = None,
                  sha256: str = "", receipt_reference: str = "", expected_binding_sha256: str = "", recipient_sha256: str = "") -> dict[str, Any]:
    """Record a manual delivery of the exact attorney-approved archive; send no message."""
    import client_file
    from portal.store import PortalStore

    who = _need(who)
    _attorney(role, "records the file handed over")
    client_file.allowed(client_dir)
    rec = read(client_dir)
    f = rec.get("file") or {}
    if not sha256 or sha256 != f.get("sha256") or (f.get("approved") or {}).get("sha256") != sha256:
        raise ValueError("The attorney must review and approve this exact prepared file first.")
    if (expected_binding_sha256 != f.get("binding_sha256") or (f.get("approved") or {}).get("binding_sha256") != expected_binding_sha256
            or recipient_sha256 != (f.get("binding") or {}).get("recipient_sha256")):
        raise ValueError("Review the exact current recipient and approved file binding before recording delivery.")
    if f.get("returned"):
        raise ValueError("This file has already been recorded as handed over.")
    file_path(client_dir, Path(client_dir).parent, portal_root)
    day = _day(on, "the date the file was handed over")
    approved_day = clock.local_date((f.get("approved") or {}).get("at"))
    if day > clock.today() or approved_day is None or day < approved_day:
        raise ValueError("Actual delivery must be no earlier than this archive's current approval and cannot be in the future.")
    recipient = client_file_policy.read(client_dir, portal_root)["recipient"]
    if how not in client_file_policy.METHODS or how != recipient["method"]:
        raise ValueError("Record actual delivery using the attorney-reviewed recipient and method.")
    receipt_reference = _typed(receipt_reference, "the actual delivery receipt or acknowledgement reference", required=True)
    f["returned"] = {"on": day.isoformat(), "how": how, "by": who, "role": role, "at": clock.stamp(), "sha256": sha256,
                     "binding_sha256": expected_binding_sha256, "recipient_sha256": recipient_sha256,
                     "recipient": recipient, "receipt_reference": receipt_reference}
    _save(client_dir, rec, "file_handed_over", f"Recorded manual handover of the attorney-approved client file; SHA256 {sha256}", who, role)
    root = _portal_root(portal_root)
    if (root / "clients" / Path(client_dir).name / "profile.json").is_file():
        store = PortalStore(root)
        e = store.engagement(Path(client_dir).name)
        e["file_sent"] = True
        store.save_engagement(Path(client_dir).name, e)
    return view(client_dir, portal_root, role)


def file_path(client_dir: Path, data_root: Path, portal_root: Path | None = None) -> Path:
    """Review download: exact prepared archive, available to a gated attorney only."""
    import client_file
    import signing_evidence

    client_file.allowed(client_dir)
    f = read(client_dir).get("file") or {}
    current = client_file_policy.handover_binding(client_dir, portal_root)
    if f.get("binding") != current or f.get("binding_sha256") != client_file_policy.digest(current):
        raise ValueError("This client file predates or differs from the current policy/inventory/recipient review; prepare it again.")
    name = f.get("name") or ""
    path = exports_folder(data_root) / name
    if not EXPORT_NAME.fullmatch(name) or not client_file.safe(path, exports_folder(data_root)):
        raise LookupError("The client's file has not been made yet.")
    client_file.inspect(path, f.get("sha256") or "")
    for letter in read(client_dir).get("letters", []):
        if (letter.get("signature") or {}).get("how") == "portal" and not signing_evidence.verify(client_dir, letter["id"])["ok"]:
            raise ValueError("The signed engagement evidence is unverifiable. Review it before downloading or handing over the file.")
    return path


def approve_consent(client_dir: Path, text: str, translator: str, who: str, role: str | None = None,
                    portal_root: Path | None = None) -> dict:
    """An attorney explicitly approves the case language wording before sending."""
    import signing_evidence

    who = _need(who)
    _attorney(role, "approves electronic-signature consent wording")
    rec = read(client_dir)
    a = agreement(rec)
    if a and (a.get("sent") or a.get("signature")):
        raise ValueError("Approve consent before sending a fresh agreement.")
    lang = language(client_dir, portal_root)
    translator = _typed(translator, "the certified translator's name", required=lang != "en")
    text = _typed(text, "the consent wording", required=True)
    rec["electronic_consent"] = {"text": text, "language": lang, "by": who, "at": clock.stamp(), "translator": translator or None,
                                 "sha256": signing_evidence.digest(text.encode("utf-8"))}
    _save(client_dir, rec, "consent_approved", "Approved the case language electronic-signature consent wording", who, role)
    return view(client_dir, portal_root, role)


# -- the keeping date passed: the destruction list ----------------------------------------------------------------------------------


def _destroyed_path(data_root: Path) -> Path:
    return Path(data_root).parent / DESTROYED_FILE


def destroyed(data_root: Path) -> list[dict[str, Any]]:
    p = _destroyed_path(data_root)
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("cases") if p.exists() else []
    except (OSError, ValueError):
        rows = []
    return rows if isinstance(rows, list) else []


def due(data_root: Path) -> dict[str, Any]:
    """Settings, Keeping current: the ended cases whose keeping date has passed and that no attorney has marked destroyed (the product never
    deletes one), the ended cases with no keeping date yet, and every destruction recorded."""
    today = clock.today()
    done = {r["case"] for r in destroyed(data_root)}
    passed, undated = [], []
    for d in sorted(p for p in Path(data_root).iterdir() if (p / FILE).exists()) if Path(data_root).is_dir() else []:
        end_ = read(d).get("end")
        if not end_ or end_.get("state") not in ENDED or d.name in done:
            continue
        keep = retention(d, date.fromisoformat(end_["on"]))  # from the end date and the office's setting as it is now
        row = {"case": d.name, "name": client_name(d), "state": end_["state"], "state_name": STATES[end_["state"]], "ended_on": end_["on"],
               "until": keep.get("until"), "words": keep.get("words"), "planning_only": not keep.get("approved", False),
               "destruction_ready": __import__("purge").destruction_status(d)["destruction_ready"], "file": bool(read(d).get("file"))}
        if not keep.get("until"):
            undated.append(row)
        elif date.fromisoformat(keep["until"]) < today:
            passed.append(row)
    return {"passed": passed, "undated": undated, "destroyed": list(reversed(destroyed(data_root)))}


def mark_destroyed(data_root: Path, case: str, who: str, role: str | None = None, folder_removed: bool = False, export_kept: bool = False,
                   note: str = "") -> dict[str, Any]:
    from portal.communication_consent import data_gate
    import jobs
    _need(who)
    _attorney(role, "records a file as destroyed")
    if not isinstance(case, str) or not case or Path(case).name != case or case in (".", "..") or "\\" in case:
        raise ValueError("Choose the exact current case.")
    with data_gate(Path(data_root).parent), jobs.case_lock(jobs.folder_for(data_root), case, timeout=30):
        client_file.allowed(Path(data_root) / case)
        return _mark_destroyed(data_root, case, who, role, folder_removed, export_kept, note)


def _mark_destroyed(data_root: Path, case: str, who: str, role: str | None = None, folder_removed: bool = False, export_kept: bool = False,
                    note: str = "") -> dict[str, Any]:
    """An attorney records that a case's file was destroyed after its keeping date: who, when, and what (the case's folder removed from the
    clients folder by the firm's IT, the export kept or not). The product does not delete the case folder. When folder removal is attested,
    the case's labelled examples and its own evaluation authorization/candidate copies are removed from their separate namespaces under the
    current legal authority. Counts are recorded only after safe cleanup succeeds; an unresolved cleanup refuses the completed marker."""
    who = _need(who)
    _attorney(role, "records a file as destroyed")
    authority = client_file_policy.require_destruction(Path(data_root) / case)
    rows = {r["case"]: r for r in due(data_root)["passed"]}
    if case not in rows:
        raise LookupError("That case is not on the list of files past their keeping date.")
    row = rows[case] | {"by": who, "role": role, "at": clock.stamp(), "folder_removed": bool(folder_removed), "export_kept": bool(export_kept),
                        "note": _typed(note, "a note")}
    row.pop("name", None)  # the record keeps the case's id and dates, never the client's name: it outlives the folder
    row.pop("words", None)
    row["policy_authority"] = authority
    if folder_removed:  # the case's labelled examples (src/reader_examples.py) hold its values: they go with its folder
        import evaluation_artifacts
        import reader_examples

        try:
            row["evaluation_artifacts_removed"] = evaluation_artifacts.remove_case(Path(data_root).parent, case)
        except (OSError, ValueError):
            raise ValueError("The case's evaluation artifacts could not be removed safely. Cleanup remains unresolved; no completed destruction record was created.") from None
        row["examples_removed"] = reader_examples.remove_case(Path(data_root) / case)
    with _LOCK:
        p = _destroyed_path(data_root)
        data = {"version": 1, "cases": destroyed(data_root) + [row]}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    try:  # the case's passages and vectors leave Find across the firm's index now, not at its next build (src/find.py)
        import find

        find.forget(data_root, case)
    except Exception as exc:  # noqa: BLE001 -- the record of the destruction stands; the next build leaves the case out (it is recorded destroyed)
        sys.stderr.write(f"find index: a destroyed case not taken out yet ({type(exc).__name__})\n")
    d = Path(data_root) / case
    if (d / FILE).exists():
        rec = read(d)
        rec["destroyed"] = {k: row[k] for k in ("by", "role", "at", "folder_removed", "export_kept", "note")}
        _save(d, rec, "destroyed", "Recorded the case's file as destroyed after its keeping date"
              + (": the folder removed" if folder_removed else "") + (", the export kept" if export_kept else ""), who, role)
    else:
        events.record("engagement", "destroyed", "Recorded the case's file as destroyed after its keeping date", case=case, home=Path(data_root).parent,
                      who=who, role=role)
    return due(data_root)


# -- the PDF --------------------------------------------------------------------------------------------------------------------


def pdf_path(client_dir: Path, letter: dict[str, Any]) -> Path:
    return Path(client_dir) / FOLDER / f"{letter['kind'].replace('_', '-')}-{letter['id']}.pdf"


def _draft_mark(p) -> None:
    p.ops[:0] = ["q 0.88 g BT /F2 110 Tf 0.766 0.643 -0.643 0.766 120 190 Tm (DRAFT) Tj ET Q"]


def _time(at: Any) -> str:
    t = clock.local(at)
    return t.strftime("%I:%M %p").lstrip("0") if t else ""


def signature_words(letter: dict[str, Any]) -> list[str]:
    """How the agreement was signed and counter-signed, in words: what the PDF and the case page say."""
    out = []
    s = letter.get("signature")
    if s and s.get("how") == "portal":
        out.append(f"Signed by the client in the portal: typed name “{s.get('typed_name')}”, on {_us(s.get('at'))} at {_time(s.get('at'))} "
                   f"(the office's time), from the internet address {s.get('address') or 'not known'}, reading the letter in "
                   f"{LANGUAGE_NAMES.get(s.get('language') or 'en', s.get('language'))}. The client ticked “I have read and agree” and typed their name.")
    elif s and s.get("how") == "paper":
        out.append(f"Signed by the client on paper on {_us(s.get('on'))}. The signed scan is kept with this letter (recorded by {s.get('by')} on {_us(s.get('at'))}).")
    c = letter.get("countersignature")
    if c:
        out.append(f"Counter-signed for the firm by “{c.get('typed_name')}” on {_us(c.get('at'))} at {_time(c.get('at'))} (the office's time), "
                   f"recorded by {c.get('by')}.")
    return out


def render(client_dir: Path, letter: dict[str, Any]) -> Path:
    """engagement/<kind>-<id>.pdf: the letterhead, the date, the letter in English, then in the client's language when it was made in it, and
    for an agreement the signatures (or the lines to sign on). DRAFT on every page, with the reason at the foot, until the attorney approves the
    letters' wording."""
    from pypdf import PdfWriter

    from fill.continuation import HEIGHT, MARGIN, WIDTH
    from review.bundle import _Sheet, _wrap

    draft = not letter.get("approved") and not approved()
    width, size, lead = WIDTH - 2 * MARGIN, 10.5, 14
    pages: list = []
    y = 0.0

    def new_page():
        nonlocal y
        p = _Sheet()
        if draft:
            _draft_mark(p)
        pages.append(p)
        y = HEIGHT - 50
        return p

    p = new_page()

    def line(text: str, font: str = "F3", fsize: float = size, gap: float = 0, indent: float = 0) -> None:
        nonlocal p, y
        for part in _wrap(text, fsize, width - indent):
            if y < 80:
                p = new_page()
            p.text(MARGIN + indent, y, part, font, fsize)
            y -= lead if fsize >= size else fsize + 3
        y -= gap

    head = letter.get("letterhead") or {}
    line(head.get("name") or "", "F2", 14)
    for extra in (head.get("tagline"), head.get("address"), head.get("attorneys")):
        if extra:
            line(extra, "F3", 8)
    y -= 4
    p.line(MARGIN, y, WIDTH - MARGIN, y)
    y -= 22
    line(_us(letter["date"]), gap=6)
    line(letter.get("client") or "", gap=12)
    line(letter["titles"]["en"], "F2", 13, gap=8)
    for para in letter["texts"]["en"]:
        line(para, gap=7)
    y -= 6
    signer = letter.get("signer") or {}
    line("Sincerely,", gap=18)
    if signer.get("name"):
        line(signer["name"], "F2")
    for extra in signer.get("lines") or []:
        line(extra, "F3", 8)
    other = next((lg for lg in letter["texts"] if lg != "en"), None)
    if other:
        y -= 16
        how = "a machine translation" if letter.get("translation") == "machine" else "the firm's translation"
        line(f"The same letter in {LANGUAGE_NAMES[other]} ({how}), as the client reads it", "F2", 11, gap=6)
        line(letter["titles"][other], "F2", 11, gap=6)
        for para in letter["texts"][other]:
            line(para, gap=7)
    if letter["kind"] == "engagement":
        y -= 12
        line("Signatures", "F2", 11, gap=6)
        said = signature_words(letter)
        for words in said:
            line(words, gap=6)
        if not letter.get("signature"):
            for who in ("Client", "For the firm"):
                if y < 120:
                    p = new_page()
                y -= 20
                p.line(MARGIN, y, MARGIN + 260, y)
                p.line(MARGIN + 300, y, WIDTH - MARGIN, y)
                p.text(MARGIN, y - 11, f"{who}: signature", "F3", 8)
                p.text(MARGIN + 300, y - 11, "Date (MM/DD/YYYY)", "F3", 8)
                y -= 16
        elif not letter.get("countersignature"):
            line("For the firm: not counter-signed yet.", gap=4)
    machine = letter.get("translation") == "machine"
    for i, page in enumerate(pages):
        foot = ("DRAFT: the wording of this letter is the attorney's to approve (Keeping current)." if draft
                else f"Made by {letter.get('made_by')} on {_us(letter.get('made_at'))}.")
        if machine:
            foot += f" The {LANGUAGE_NAMES[other]} is a machine translation until the firm's certified translator replaces it."
        for n, part in enumerate(_wrap(foot, 7.5, width - 60)):
            page.text(MARGIN, 40 - n * 9, part, "F2", 7.5)
        page.text(WIDTH - MARGIN - 56, 22, f"Page {i + 1} of {len(pages)}", "F3", 7)
    writer = PdfWriter()
    for page in pages:
        writer.add_page(page.to_page(writer))
    writer.add_metadata({"/Title": letter["titles"]["en"]})
    buf = io.BytesIO()
    writer.write(buf)
    out = pdf_path(client_dir, letter)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return out


def letter_pdf(client_dir: Path, letter_id: str, paper_copy: bool = False) -> Path:
    """A letter's PDF (drawn again if it is missing), or the paper scan the client signed."""
    letter = _find(read(client_dir), letter_id)
    if paper_copy:
        name = (letter.get("signature") or {}).get("file")
        path = Path(client_dir) / FOLDER / str(name or "")
        if not name or not path.is_file():
            raise LookupError("There is no paper copy of this letter.")
        return path
    path = pdf_path(client_dir, letter)
    return path if path.is_file() else render(client_dir, letter)


# -- the case page's panel ------------------------------------------------------------------------------------------------------------


def _letter_row(letter: dict[str, Any]) -> dict[str, Any]:
    return {"id": letter["id"], "kind": letter["kind"], "name": KINDS[letter["kind"]], "made_by": letter.get("made_by"), "made_at": letter.get("made_at"),
            "office": letter.get("office_name"), "filings": letter.get("filings") or [], "fee": letter.get("fee"), "government_fees": letter.get("government_fees"),
            "additions": letter.get("additions"), "language": LANGUAGE_NAMES.get(letter.get("language") or "en"),
            "in_language": len(letter.get("texts") or {}) > 1, "sent": letter.get("sent"), "signature": letter.get("signature"),
            "countersignature": letter.get("countersignature"), "signed_words": signature_words(letter),
            "paper": bool((letter.get("signature") or {}).get("file")), "draft": not letter.get("approved") and not approved(),
            "stale": not _wording_matches(letter)}


def _wording_matches(letter: dict[str, Any]) -> bool:
    try:
        return letter.get("wording_hash") == document(letter["office"], letter["kind"])["wording_hash"]
    except (LookupError, KeyError):
        return False


def view(client_dir: Path, portal_root: Path | None = None, role: str | None = None) -> dict[str, Any]:
    """The case page's Agreement and closing panel."""
    sync(client_dir, portal_root)
    rec = read(client_dir)
    end_ = rec.get("end")
    office = _office(client_dir)
    lang = language(client_dir, portal_root)
    in_portal = (_portal_root(portal_root) / "clients" / Path(client_dir).name / "profile.json").exists()
    agree = agreement(rec)
    policy = client_file_policy.display(client_dir, portal_root)
    file = dict(rec["file"]) if rec.get("file") else None
    if file is not None:
        current = policy["handover"].get("binding")
        file["binding_current"] = bool(current and file.get("binding") == current
                                       and file.get("binding_sha256") == client_file_policy.digest(current))
        file["integrity_current"] = False
        if file["binding_current"]:
            try:
                file_path(client_dir, Path(client_dir).parent, portal_root)
                file["integrity_current"] = True
            except (ValueError, LookupError, OSError, PermissionError, TypeError, KeyError):
                pass
        file["integrity_hold"] = None if file["integrity_current"] else "This archive is missing, changed or no longer bound to current review; prepare it again."
        file["approval_current"] = bool(file["binding_current"] and file["integrity_current"] and (file.get("approved") or {}).get("sha256") == file.get("sha256")
                                        and (file.get("approved") or {}).get("binding_sha256") == file.get("binding_sha256"))
    out_end = None
    if end_:
        out_end = dict(end_) | {"state_name": STATES[end_["state"]], "letter_name": KINDS[LETTER_FOR[end_["state"]]],
                                "retention_words": retention(client_dir, date.fromisoformat(end_["on"]))["words"]}
    return {"state": end_["state"] if end_ else "open", "state_name": STATES[end_["state"] if end_ else "open"], "end": out_end,
            "letters": [_letter_row(x) for x in reversed(rec["letters"])], "agreement": _letter_row(agree) if agree else None,
            "file": file if role != "paralegal" else None, "file_policy": policy,
            "file_history": rec.get("file_history") if role != "paralegal" else None, "destroyed": rec.get("destroyed"),
            "consent_draft": __import__("signing_evidence").CONSENT_DRAFT.get(lang), "consent_approval": rec.get("electronic_consent"),
            "signing_evidence": __import__("signing_evidence").verify(client_dir, agree["id"]) if agree and (agree.get("signature") or {}).get("how") == "portal" else None,
            "history": [h | {"state_name": STATES.get(h.get("state"), h.get("state"))} for h in reversed(rec.get("history") or [])],
            "filings": filing_choices(), "packet_fees": packet_fees(client_dir), "deadlines": known_deadlines(client_dir),
            "office": office["name"], "language": LANGUAGE_NAMES.get(lang, lang), "in_portal": in_portal,
            "translation_ready": lang == "en" or document(office["id"], "engagement")["current"].get(lang, False),
            "practice": practice(), "can_end": role != "paralegal", "can_send": role != "paralegal", "retention_now": retention(client_dir, clock.today())["words"],
            "states": [[k, v] for k, v in STATES.items() if k != "open"], "conflict_declined": None if end_ else conflict_declined(client_dir),
            "processed": (Path(client_dir) / "fact_graph.json").exists()}
