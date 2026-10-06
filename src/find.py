"""Find across the firm (brief N1): the firm-wide index of meaning, beside the search index.

One SQLite file (data/find.db; I485_FIND points elsewhere), built the way the search index is (src/index.py): from what the product already
keeps, never from the scans, and never the source of truth. Delete it and the follower or the nightly run builds it again; tools/find_index.py
builds it from scratch. What goes in, per case:

    page          every page of every document record's text (documents.json "text", cut at the page breaks the reader left)
    translation   the office's English translation of that page beside it ("translated"): Haitian Creole is weak in every embedding model,
                  so a question in English finds the client's Creole or Portuguese document through the office's English
    decision      every step of every review decision: the item's title, what was done, and the reviewer's note (decisions.json)
    note          every case note (notes.json), with the attorney-only mark an attorney may set on it
    wording       every approved wording of the firm's library (src/wordings.py): the firm's, on no case

Each is cut into passages of at most 300 characters, MASKED (Masker: every name, A-Number, receipt, passport and Social Security number, date,
street address, phone number and e-mail address becomes a label such as [name]), and only then embedded. The masked passage is what the file
keeps and what a person reads: the unmasked text is never in the file, so a name typed as a question finds nothing (docs/research/ai_stack_2026.md,
N1: the search leak), and a stolen file holds no name to invert. The file is still the firm's work product and as protected as the cases:
0600, backed up with them, never in any export a client receives, and a deleted case's rows leave it at once (forget()).

The model ranks; a person reads. Nothing a model writes is ever shown: each hit is a verbatim passage of the firm's own records, with where it is
(the case, the document and page, or the decision's or note's date) and one plain line saying why it matched, made here from the words the
question and the passage share.

The gate is applied BEFORE ranking (search): every row is scored, then every row the reader may not see is blanked (a restricted case they are
not named on, a confidential document in a case they may open but are not named on, an attorney-only note for anyone but an attorney, a wording
used only on cases they may not open, a case whose folder is gone), and only then are the top 20 taken. A hidden case's passage can never appear,
and nor can its score or a count of it: the list never says "and 2 more you may not open". Each hit is then asked of may_open once more.

The embedder (Embedder) is the firm's own Ollama on this machine (OllamaEmbedder: FIND_MODEL, default qwen3-embedding:0.6b, through the client
and the settings src/drafting.py uses: OLLAMA_URL, the WSL fallback, the one-use-at-a-time lock), or the deterministic HashingEmbedder
(I485_FIND_EMBEDDER=hashing: every test, and any machine without the model). The vectors remember which model made them: another model, and
the file is built again.

    rebuild(client_dir)            replace one case's rows (the follower calls it for the cases the event ledger names)
    rebuild_changed(clients_root)  only the cases whose files changed since their rows were built, and the wording library (the nightly run)
    rebuild_all(clients_root)      from scratch (tools/find_index.py)
    forget(clients_root, case)     a case's rows out at once (a case recorded destroyed, a folder taken away)
    search(question, ...)          Find across the firm on the Search page
    keep_up(clients_root)          what the review app calls before a question: the follower (src/keepup.py), in the background
    log_question(...)              who asked what and when (data/find_questions.jsonl: the attorney's to read; the ledger row has the length only)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

import clock
import events
import keepup

REPO = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1  # raise when the tables change: an old file is dropped and built again (it is only a copy)
PASSAGE = 300  # characters in one passage, at most (what a hit shows, verbatim)
TOP = 20  # hits a question returns
MAX_QUESTION = 500  # characters in one question
BATCH = 32  # passages embedded in one call to the model
QUESTIONS = "find_questions.jsonl"  # who asked what: beside the case folders, the attorney's to read
SETTING = ("drafting", "find_across")  # the attorney's switch (Settings, Drafting and models): off unless switched on
# the files a case's rows are built from: a change in any of them builds the case again (status.json and access.json: what makes a document confidential;
# engagement.json: a case recorded destroyed has no rows)
SIGNED = ("documents.json", "decisions.json", "notes.json", "fact_graph.json", "status.json", "meta.json", "conflict_check.json", "engagement.json")
KINDS = {"page": "a page of a document", "translation": "the office's English translation of a page", "decision": "a review decision",
         "note": "a case note", "wording": "an approved wording of the firm's library"}

# What each table is, for the data dictionary (docs/data_dictionary.md); the columns' sentences are the comments in SCHEMA.
TABLES = {
    "passages": "One row per passage of the firm's records: a page of a document or its English translation, a review decision, a case note or an approved "
                "wording, masked before it was embedded (no name, number, date or address), with its embedding. Holds the firm's work product.",
    "cases": "One row per case indexed (and one for the firm's wording library), with what its rows were built from.",
    "meta": "Which model made the vectors, and a counter raised at every change (the review app's copy in memory is read again when it moves).",
}

SCHEMA = """
create table if not exists passages (
  id integer primary key,      -- the row's number
  case_id text not null,       -- the case it belongs to; empty for a wording of the firm's library
  kind text not null,          -- page, translation, decision, note or wording
  ref text,                    -- the document record's id, the decision's item id, the note's id or the wording's id
  page integer,                -- the page of the document, 1-based (page and translation only)
  file text,                   -- the document's file, to open it at that page
  label text,                  -- what the source is in words: the kind of document, the decision's title, the answer a wording explains
  at text,                     -- when: the decision's or the note's date, the wording's approval (with its offset)
  confidential integer,        -- 1 for a document the law keeps confidential (8 U.S.C. 1367, 8 CFR 208.6): an attorney and the staff named on the case only
  attorney_only integer,       -- 1 for a note an attorney marked for attorneys only
  opened text,                 -- when the case was opened (its first document), for newest case first among equal scores
  text text not null,          -- the passage as embedded and as shown: at most 300 characters, masked (names, numbers, dates and addresses replaced by a label)
  vector blob not null         -- its embedding: float32, unit length
);
create index if not exists passages_case on passages(case_id);
create table if not exists cases (
  case_id text primary key,    -- the case's id (the client's folder name); empty for the firm's wording library
  signature text,              -- what the rows were built from: a change means they are built again
  built text                   -- when the rows were built (with its offset)
);
create table if not exists meta (
  key text primary key,        -- embedder (the model the vectors were made with) or generation (raised at every change)
  value text                   -- its value
);
"""


class FindDamaged(Exception):
    """The file could not be read; it was set aside and the next build makes a new one."""


class ModelUnavailable(RuntimeError):
    """The embedding model on this machine did not answer (Ollama not running, or the model not pulled)."""


# -- the embedder --------------------------------------------------------------------------------------------------------------------------


class Embedder:
    """What turns passages and questions into unit vectors. name: the model the vectors are made with (stored in the file: another name, and the
    file is built again). floor: the least cosine a hit needs; below it nothing is close enough to show."""

    name = "embedder"
    floor = 0.3

    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover -- each embedder says how
        raise NotImplementedError

    def passages(self, texts: list[str]) -> np.ndarray:
        return _unit(self.embed(texts))

    def question(self, text: str) -> np.ndarray:
        return _unit(self.embed([text]))[0]


def _unit(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    if m.ndim == 1:
        m = m[None, :]
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


_STOP = frozenset("""a an and are as at be by de da do dos das del la el los las en e o os um uma y que for from has have he her his i if in is it its
    me my no not of on or our she so that the their them they this to was we were what when where which who why will with you your
    sou foi sua seu com para por uma nos nas ao aos ela ele eles elas mi su con por para lo le se es un una al
    li mwen ou nou yo ki pou nan ak sa pa te""".split())


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(text or "")) if unicodedata.category(c) != "Mn").lower()


def words(text: str) -> list[str]:
    """The content words of a text, accents and case folded, the commonest small words left out (the hashing embedder's features, and the words a
    question and a hit share)."""
    return [w for w in re.findall(r"[a-z0-9]+", _fold(text)) if len(w) > 1 and w not in _STOP and not w.isdigit()]


class HashingEmbedder(Embedder):
    """Deterministic and offline: each content word is hashed to one of `dims` places with a sign. Two texts are close when they share words. For
    the tests and for a machine without the model; it knows nothing of meaning, only of shared words."""

    floor = 0.15

    def __init__(self, dims: int = 4096):
        self.dims = dims
        self.name = f"hashing:{dims}"

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dims), dtype=np.float32)
        for i, text in enumerate(texts):
            for w in words(text):
                h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=8).digest(), "little")
                out[i, h % self.dims] += 1.0 if (h >> 32) & 1 else -1.0
        return out


class OllamaEmbedder(Embedder):
    """The firm's own model through its Ollama on this machine (nothing leaves the machine): POST /api/embed, the client src/drafting.py uses for
    the local model (src/vision/ollama.py), OLLAMA_URL, the WSL fallback through Windows, and the one-use-at-a-time lock (src/jobs.py gpu_lock).
    FIND_MODEL names the model (qwen3-embedding:0.6b; bge-m3 is the alternative the research tied with it). FIND_FLOOR overrides the floor (0.45, provisional: measured 10/05/2026 on a made-up firm, docs/decisions.md)
    once the firm has measured it with its own questions."""

    QWEN_TASK = "Instruct: Given a question from a law firm's staff, retrieve passages of the firm's case records that answer it\nQuery: "

    def __init__(self, model: str | None = None, timeout: float = 120.0):
        self.model = model or os.environ.get("FIND_MODEL", "qwen3-embedding:0.6b")
        self.name = f"ollama:{self.model}"
        self.timeout = timeout
        try:
            self.floor = float(os.environ.get("FIND_FLOOR", "0.45"))
        except ValueError:
            self.floor = 0.45

    def embed(self, texts: list[str]) -> np.ndarray:
        from vision.ollama import DEFAULT_URL, _is_wsl, _post_http, _post_via_windows_curl

        import jobs

        endpoint = os.environ.get("OLLAMA_URL", DEFAULT_URL).rstrip("/") + "/api/embed"
        payload = {"model": self.model, "input": list(texts)}
        with jobs.gpu_lock():
            try:
                try:
                    result = _post_http(endpoint, payload, self.timeout)
                except OSError:
                    if not _is_wsl():
                        raise
                    result = _post_via_windows_curl(endpoint, payload, self.timeout, Path(".ocr_tmp"))
            except (OSError, ValueError) as exc:
                raise ModelUnavailable(f"The model {self.model} on this machine did not answer ({type(exc).__name__}).") from exc
        vectors = result.get("embeddings") if isinstance(result, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ModelUnavailable(f"The model {self.model} gave no vectors ({str((result or {}).get('error') or 'no answer')[:120]}).")
        return np.asarray(vectors, dtype=np.float32)

    def question(self, text: str) -> np.ndarray:
        # Qwen3-Embedding is trained with the task said before the question (and nothing before a passage); bge-m3 takes the question as it is
        return _unit(self.embed([(self.QWEN_TASK if "qwen3" in self.model.lower() else "") + text]))[0]


def embedder() -> Embedder:
    """The embedder this machine uses: I485_FIND_EMBEDDER=hashing for the deterministic one, else the firm's Ollama model."""
    return HashingEmbedder() if os.environ.get("I485_FIND_EMBEDDER", "").lower() == "hashing" else OllamaEmbedder()


# -- masking -------------------------------------------------------------------------------------------------------------------------------

MONTHS = ("january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec "
          "janeiro fevereiro marco março abril maio junho julho agosto setembro outubro novembro dezembro fev abr mai ago set out dez "
          "enero febrero marzo mayo junio julio septiembre octubre noviembre diciembre ene "
          "janvye fevriye mas avril me jen jiye out septanm oktob novanm desanm")
_MONTH = "(?:" + "|".join(sorted({re.escape(m) for m in MONTHS.split()}, key=len, reverse=True)) + r")\.?"
_DATES = [
    re.compile(r"\b\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}\b"),
    re.compile(r"(?i)\b\d{1,2}(?:st|nd|rd|th|º|o)?\s+(?:de\s+)?" + _MONTH + r"(?:\s+(?:de\s+|of\s+)?\d{2,4})?\b"),
    re.compile(r"(?i)\b" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{2,4}\b"),
    re.compile(r"(?i)\b" + _MONTH + r"\s+(?:de\s+|of\s+)?\d{4}\b"),
]
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,3}\)\s?|\d{2,3}[\s.-])\d{3,5}[\s.-]\d{4}(?!\w)")
_STREET = re.compile(r"(?i)\b\d{1,6}[A-Z]?\s+(?:[A-Za-zÀ-ÿ'.]+\s+){0,5}(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl|"
                     r"terrace|circle|highway|hwy|parkway|pkwy)\b\.?(?:,?\s+(?:apt|apartment|unit|suite|#)\.?\s*[\w-]+)?")
_STREET_LATIN = re.compile(r"(?i)\b(?:rua|r\.|avenida|av\.|travessa|estrada|calle|carrera|ri|wout)\s+[^\n,;]{2,60}?(?:,?\s*(?:n[ºo°.]?\s*)?\d{1,6})")
_ZIP = re.compile(r"\b\d{5}(?:-\d{3,4})?\b")
_A_NUMBER = re.compile(r"(?i)\bA[\s#-]*\d{3}[\s-]?\d{3}[\s-]?\d{2,3}\b")
_NUMBERISH = re.compile(r"\b[A-Z]{0,4}\d[\d\s-]{4,}\d\b")
_LABELLED = re.compile(r"(?i)\b(name|nome|nombre|non|father|mother|pai|m[ãa]e|padre|madre|papa|manman|filia[çc][ãa]o|declarant|applicant|beneficiary|"
                       r"petitioner|respondent|child|filho|filha|hijo|hija|pitit|spouse|esposa?|marido)(\s*(?:'s\s+name)?\s*[:\-]\s*)"
                       r"((?:[A-ZÀ-Þ][\w'À-ÿ-]+)(?:\s+(?:(?:de|da|do|dos|das|del|la|y|e)\s+)?[A-ZÀ-Þ][\w'À-ÿ-]+){0,6})")
_NAME_STOP = frozenset({"the", "and", "case", "new", "client", "applicant", "brasil", "brazil", "state", "court", "united", "states", "office"})
NAME_KEYS = re.compile(r"(given_name|family_name|middle_name|full_name|other_names?|maiden|surname|\.name)$")
VALUE_KEYS = re.compile(r"(street|address|apt|unit|zip|postal|phone|email|date_of_birth|birth_date|dob|a_number|alien_number|passport|receipt|ssn|social_security)")


class Masker:
    """Masks a case's text before it is embedded: what the case's records know of its people (every spelling of every name in the people index
    the conflict search reads, src/people.py; the names, dates of birth, numbers and addresses in its fact graph; the words of the case's own id,
    which can be a name), and whatever looks like a person's data anywhere (dates, A-Numbers, receipt and passport numbers, any long number,
    street addresses, ZIP codes, phone numbers, e-mail addresses, a name after "Name:" or "Mãe:"). The labels: [name], [date], [number],
    [address], [phone], [email]."""

    def __init__(self, names: Iterable[str] = (), values: Iterable[str] = (), keep: Iterable[str] = ()):
        """keep: words never masked alone (the staff's own names, for src/support.py: a client called Ana does not hide Ana Attorney)."""
        kept = {w for k in keep for w in re.findall(r"[a-z0-9']+", _fold(k))}
        whole, single = set(), set()
        for n in names:
            folded = " ".join(re.findall(r"[a-z0-9']+", _fold(n)))
            if len(folded) >= 3 and folded not in kept:  # a name of one word that is a kept word (a staff member's first name) is not masked alone either
                whole.add(folded)
            single |= {w for w in folded.split() if len(w) >= 3 and w not in _NAME_STOP and w not in _STOP and w not in kept}
        self.names = sorted(whole | single, key=len, reverse=True)
        self.values = sorted({v.strip() for v in values if v and len(v.strip()) >= 4}, key=len, reverse=True)
        pattern = "|".join(re.escape(n).replace(r"\ ", r"[\s,.'-]+") for n in self.names)
        self._names = re.compile(r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])") if pattern else None

    def __call__(self, text: str) -> str:
        text = str(text or "")
        for v in self.values:  # a value the case's records hold, exactly as written (an address line, a number with its spaces)
            text = re.sub(re.escape(v).replace(r"\ ", r"\s+"), "[value]", text, flags=re.I)
        text = text.replace("[value]", "[number]")
        text = _EMAIL.sub("[email]", text)
        text = _LABELLED.sub(lambda m: m.group(1) + m.group(2) + "[name]", text)
        text = _STREET.sub("[address]", text)
        text = _STREET_LATIN.sub("[address]", text)
        for d in _DATES:
            text = d.sub("[date]", text)
        text = _PHONE.sub("[phone]", text)
        text = _A_NUMBER.sub("[number]", text)
        text = events.IDENTIFIER.sub("[number]", text)
        text = _NUMBERISH.sub("[number]", text)
        text = _ZIP.sub("[number]", text)
        if self._names is not None:
            text = self._mask_names(text)
        return re.sub(r"(?:\[(name|number|date|address)\][\s,]*){2,}", lambda m: f"[{m.group(1)}] ", text).strip()

    def _mask_names(self, text: str) -> str:
        # matched on the folded text (one character for each character of the original), replaced in the original
        folded = "".join((_fold(c) or c)[0] for c in text)
        spans = [(m.start(), m.end()) for m in self._names.finditer(folded)]
        out, last = [], 0
        for s, e in spans:
            out += [text[last:s], "[name]"]
            last = e
        return "".join(out + [text[last:]])


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, dict):
        return [s for k, v in value.items() if k != "sources" for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def masker_for(client_dir: str | Path) -> Masker:
    """The case's Masker: its people (src/people.py), its fact graph's names and personal values, and its id's words."""
    return Masker(*masker_inputs(client_dir))


def masker_inputs(client_dir: str | Path) -> tuple[list[str], list[str]]:
    """(names, values) a case's Masker is made of (src/support.py makes one of every case's)."""
    client_dir = Path(client_dir)
    names: list[str] = [w for w in re.split(r"[^A-Za-zÀ-ÿ]+", client_dir.name) if len(w) >= 3]
    values: list[str] = []
    try:
        import people

        for p in people.rows(client_dir):
            names += [s for n in p.get("names") or [] for s in _strings({k: n.get(k) for k in ("name", "given", "family")} if isinstance(n, dict) else n)]
            for field in ("a_numbers", "passports", "birth_dates"):
                values += _strings(p.get(field))
    except Exception:  # noqa: BLE001 -- the fact graph below and the patterns still mask
        pass
    graph = _read(client_dir / "fact_graph.json")
    facts = graph.get("facts") if isinstance(graph, dict) and isinstance(graph.get("facts"), dict) else {}
    for key, f in facts.items():
        if not isinstance(f, dict):
            continue
        found = [f.get("value")] + [s.get(k) for s in f.get("sources") or [] if isinstance(s, dict) for k in ("raw_value", "normalized_value")]
        if NAME_KEYS.search(key):
            names += [s for x in found for s in _strings(x)]
        elif VALUE_KEYS.search(key):
            values += [s for x in found for s in _strings(x)]
    check = _read(client_dir / "conflict_check.json")
    if isinstance(check, dict):
        subject = check.get("subject") or {}
        names += _strings([subject.get("name"), subject.get("other_names")]) + _strings([p.get("name") for p in check.get("parties") or [] if isinstance(p, dict)])
    try:
        from review.state import display_name

        names.append(display_name(client_dir))
    except Exception:  # noqa: BLE001
        pass
    return [n for n in names if not re.fullmatch(r"(?i)new case.*", n or "")], values


# -- passages ------------------------------------------------------------------------------------------------------------------------------


def passages(text: str, size: int = PASSAGE) -> list[str]:
    """The text cut into passages of at most `size` characters, at a sentence's end where one falls, else at a space."""
    text = " ".join(str(text or "").split())
    out = []
    while text:
        if len(text) <= size:
            out.append(text)
            break
        cut = max(text.rfind(". ", 0, size), text.rfind("? ", 0, size), text.rfind("! ", 0, size), text.rfind("; ", 0, size))
        cut = cut + 1 if cut >= size // 2 else text.rfind(" ", 0, size)
        cut = cut if cut > 0 else size
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    return [p for p in out if re.search(r"\w", p)]


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _first_page(rec: dict[str, Any]) -> int:
    import index

    files = rec.get("files") if isinstance(rec.get("files"), list) else []
    return index._first_page(rec.get("pages"), str(files[0]) if files else "") or 1


def _destroyed(client_dir: Path) -> bool:
    rec = _read(client_dir / "engagement.json")
    return isinstance(rec, dict) and bool(rec.get("destroyed"))


def _opened(records: list[dict[str, Any]], client_dir: Path) -> str:
    added = sorted(str(r["added"]) for r in records if r.get("added"))
    if added:
        return added[0]
    p = client_dir / "fact_graph.json"
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(p.stat().st_mtime)) if p.exists() else ""


def _documents(client_dir: Path) -> list[dict[str, Any]]:
    try:
        import documents

        return [r for r in documents.load(client_dir)["documents"] if isinstance(r, dict) and r.get("id")]
    except Exception:  # noqa: BLE001 -- the records as written still index
        raw = _read(client_dir / "documents.json")
        return [r for r in (raw.get("documents") if isinstance(raw, dict) else None) or [] if isinstance(r, dict) and r.get("id")]


ACTION_WORDS = {"confirm": "Confirmed", "set": "Set by the reviewer", "blank": "Left blank", "acknowledge": "Acknowledged", "absent": "Recorded as absent",
                "sign_off": "Signed off", "escalate": "Sent to the attorney"}


def case_rows(client_dir: str | Path) -> list[dict[str, Any]]:
    """The case's passages before they are embedded: every page and translation, decision step and note, masked. A case recorded destroyed has none."""
    client_dir = Path(client_dir)
    if _destroyed(client_dir):
        return []
    import index

    mask = masker_for(client_dir)
    case = client_dir.name
    try:
        import documents

        case_flag = documents.case_confidentiality(client_dir)
    except Exception:  # noqa: BLE001
        case_flag = None
    records = _documents(client_dir)
    opened = _opened(records, client_dir)
    rows: list[dict[str, Any]] = []

    def add(kind: str, ref: str, text: str, **extra: Any) -> None:
        for p in passages(mask(text)):
            rows.append({"case_id": case, "kind": kind, "ref": ref, "page": None, "file": None, "label": "", "at": None, "confidential": 0,
                         "attorney_only": 0, "opened": opened} | extra | {"text": p})

    seen = set()
    for rec in records:
        if str(rec["id"]) in seen:
            continue
        seen.add(str(rec["id"]))
        files = [str(f) for f in rec.get("files") or [] if f] if isinstance(rec.get("files"), list) else []
        first = _first_page(rec)
        confidential = 1 if (case_flag or rec.get("confidential")) else 0
        label = index.type_name(rec.get("type"))
        for kind, field in (("page", "text"), ("translation", "translated")):
            for n, page in enumerate(index.scrub(rec.get(field)).split("\f")):
                add(kind, str(rec["id"]), page, page=first + n, file=files[0] if files else None, label=label, confidential=confidential)
    log = _read(client_dir / "decisions.json")
    for item_id, entry in (log.items() if isinstance(log, dict) else ()):
        if not isinstance(entry, dict):
            continue
        item = entry.get("item") or {}
        steps = entry.get("history") or [{k: v for k, v in entry.items() if k not in ("item", "history")}]
        for step in steps:
            if not isinstance(step, dict) or step.get("undone"):
                continue
            title = events.plain(str(item.get("title") or ""), 120) or "A review item"
            said = ". ".join(x for x in (title, ACTION_WORDS.get(str(step.get("action")), str(step.get("action") or "").replace("_", " ").capitalize()),
                                         str(step.get("note") or "").strip()) if x)
            add("decision", str(item_id), said, label=title, at=step.get("at"))
    notes = _read(client_dir / "notes.json")
    for n in (notes.get("notes") if isinstance(notes, dict) else None) or []:
        if isinstance(n, dict) and n.get("id") and n.get("text"):
            add("note", str(n["id"]), str(n["text"]), label="Note", at=n.get("at"), attorney_only=1 if n.get("attorney_only") else 0)
    return rows


def wording_rows(clients_root: str | Path) -> list[dict[str, Any]]:
    """The firm's approved wordings (src/wordings.py): slots only by construction, masked all the same."""
    import wordings

    mask = Masker()
    out = []
    for rec in wordings.every(wordings.root(clients_root)):
        if rec.get("status") != "approved":
            continue
        label = wordings._item_words(rec) if rec.get("key") else "A wording of the firm"
        for p in passages(mask(wordings.spoken(rec["text"]))):
            out.append({"case_id": "", "kind": "wording", "ref": rec["id"], "page": None, "file": None, "label": label, "at": (rec.get("approved") or {}).get("at"),
                        "confidential": 0, "attorney_only": 0, "opened": "", "text": p})
    return out


# -- the file ------------------------------------------------------------------------------------------------------------------------------


def default_path(clients_root: str | Path) -> Path:
    """I485_FIND, else data/find.db next to the client folders (beside index.db)."""
    env = os.environ.get("I485_FIND")
    return Path(env) if env else Path(clients_root).resolve().parent / "find.db"


def _corrupt(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.DatabaseError) and any(w in text for w in ("malformed", "not a database", "corrupt", "disk image"))


def reset(path: str | Path) -> None:
    """Sets a damaged file aside (find.db.damaged) so a new one can be made."""
    path = Path(path)
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    if path.exists():
        try:
            os.replace(path, Path(str(path) + ".damaged"))
        except OSError:
            path.unlink(missing_ok=True)


def _owner_only(path: Path) -> None:
    for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            if p.exists():
                os.chmod(p, 0o600)
        except OSError:
            pass


def connect(path: str | Path, model: Embedder | None = None) -> sqlite3.Connection:
    """The file, made if it isn't there (0600: the owner only); a damaged one is set aside and made new. model: when the vectors in it were made
    by another model, every row is dropped (they are built again)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        db = None
        try:
            if not path.exists():
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
            db = sqlite3.connect(path, timeout=30, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.execute("pragma journal_mode=wal")
            if db.execute("pragma user_version").fetchone()[0] not in (0, SCHEMA_VERSION):
                for name in ("passages", "cases", "meta"):
                    db.execute(f"drop table if exists {name}")
            db.executescript(SCHEMA)
            db.execute(f"pragma user_version = {SCHEMA_VERSION}")
            if model is not None:
                made = db.execute("select value from meta where key = 'embedder'").fetchone()
                if made is None or made[0] != model.name:
                    with db:
                        db.execute("delete from passages")
                        db.execute("delete from cases")
                        db.execute("insert or replace into meta values ('embedder', ?)", (model.name,))
                        _bump(db)
            _owner_only(path)
            return db
        except sqlite3.DatabaseError as exc:
            if db is not None:
                db.close()
            if not _corrupt(exc) or attempt == 2:
                raise FindDamaged(f"{type(exc).__name__}: {exc}") from exc
            reset(path)
    raise FindDamaged("unreachable")  # pragma: no cover


def _bump(db: sqlite3.Connection) -> None:
    db.execute("insert into meta values ('generation', '1') on conflict(key) do update set value = cast(value as integer) + 1")


def _signature(client_dir: Path) -> str:
    parts = []
    for name in SIGNED:
        p = client_dir / name
        parts.append(f"{p.stat().st_mtime_ns}:{p.stat().st_size}" if p.exists() else "-")
    return "|".join(parts)


def _wordings_signature(clients_root: Path) -> str:
    import wordings

    base = wordings.root(clients_root)
    files = sorted(base.glob("*/*/*.json")) if base.is_dir() else []
    return hashlib.sha256("|".join(f"{p}:{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in files).encode()).hexdigest()[:16]


def _store(db: sqlite3.Connection, case: str, rows: list[dict[str, Any]], model: Embedder, signature: str) -> int:
    vectors = np.zeros((0, 1), dtype=np.float32)
    texts = [r["text"] for r in rows]
    if texts:
        vectors = np.vstack([model.passages(texts[i:i + BATCH]) for i in range(0, len(texts), BATCH)]).astype(np.float32)
    with db:  # one transaction: a question never sees half a case
        db.execute("delete from passages where case_id = ?", (case,))
        db.executemany("insert into passages (case_id, kind, ref, page, file, label, at, confidential, attorney_only, opened, text, vector)"
                       " values (?,?,?,?,?,?,?,?,?,?,?,?)",
                       [(r["case_id"], r["kind"], r["ref"], r["page"], r["file"], r["label"], r["at"], r["confidential"], r["attorney_only"], r["opened"],
                         r["text"], vectors[i].tobytes()) for i, r in enumerate(rows)])
        db.execute("insert or replace into cases values (?,?,?)", (case, signature, clock.stamp()))
        _bump(db)
    return len(rows)


def _delete(db: sqlite3.Connection, case: str) -> None:
    with db:
        db.execute("delete from passages where case_id = ?", (case,))
        db.execute("delete from cases where case_id = ?", (case,))
        _bump(db)


def rebuild(client_dir: str | Path, db_path: str | Path | None = None, model: Embedder | None = None) -> int:
    """Replaces one case's rows (client_dir is data/clients/<id>): the passages indexed. A case folder that is gone, or recorded destroyed, has none."""
    client_dir = Path(client_dir)
    model = model or embedder()
    path = Path(db_path) if db_path else default_path(client_dir.parent)
    with closing(connect(path, model)) as db:
        if not client_dir.is_dir() or not ((client_dir / "documents.json").exists() or (client_dir / "fact_graph.json").exists()) \
                or client_dir.name in _destroyed_cases(client_dir.parent):
            _delete(db, client_dir.name)
            return 0
        return _store(db, client_dir.name, case_rows(client_dir), model, _signature(client_dir))


def _case_dirs(clients_root: Path) -> list[Path]:
    if not clients_root.is_dir():
        return []
    return sorted(p for p in clients_root.iterdir() if p.is_dir() and ((p / "documents.json").exists() or (p / "fact_graph.json").exists()))


def _destroyed_cases(clients_root: Path) -> set[str]:
    try:
        import engagement

        return {str(r.get("case")) for r in engagement.destroyed(clients_root)}
    except Exception:  # noqa: BLE001
        return set()


def rebuild_changed(clients_root: str | Path, db_path: str | Path | None = None, *, force: bool = False, model: Embedder | None = None) -> dict[str, int]:
    """Builds the cases whose files changed since their rows were built, and the wording library when it changed. A case folder that is gone, or a
    case recorded destroyed, loses its rows. force: every case."""
    clients_root = Path(clients_root)
    model = model or embedder()
    path = Path(db_path) if db_path else default_path(clients_root)
    out = {"rebuilt": 0, "unchanged": 0, "removed": 0, "unreadable": 0}
    with closing(connect(path, model)) as db:
        known = {r["case_id"]: r["signature"] for r in db.execute("select case_id, signature from cases")}
        destroyed = _destroyed_cases(clients_root)
        present = set()
        for d in _case_dirs(clients_root):
            if d.name in destroyed:
                continue
            present.add(d.name)
            sig = _signature(d)
            if not force and known.get(d.name) == sig:
                out["unchanged"] += 1
                continue
            try:
                _store(db, d.name, case_rows(d), model, sig)
                out["rebuilt"] += 1
            except ModelUnavailable:
                raise
            except Exception:  # noqa: BLE001 -- one odd record must not stop the other 1,799
                out["unreadable"] += 1
        for gone in set(known) - present - {""}:
            _delete(db, gone)
            out["removed"] += 1
        sig = _wordings_signature(clients_root)
        if force or known.get("") != sig:
            _store(db, "", wording_rows(clients_root), model, sig)
        out["passages"] = db.execute("select count(*) from passages").fetchone()[0]
    return out


def rebuild_all(clients_root: str | Path, db_path: str | Path | None = None, model: Embedder | None = None) -> dict[str, int]:
    """Every case and the wording library from scratch: the file is made new."""
    clients_root = Path(clients_root)
    path = Path(db_path) if db_path else default_path(clients_root)
    for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        p.unlink(missing_ok=True)
    _CACHE.pop(str(path), None)
    return rebuild_changed(clients_root, path, force=True, model=model)


def forget(clients_root: str | Path, case: str, db_path: str | Path | None = None) -> None:
    """A case's vectors and passages out of the file at once (a case recorded destroyed, a folder taken away): not at the next build."""
    path = Path(db_path) if db_path else default_path(clients_root)
    if not path.exists() or not case:
        return
    with closing(connect(path)) as db:
        _delete(db, case)
    _CACHE.pop(str(path), None)


# -- keeping it current ----------------------------------------------------------------------------------------------------------------------

_FOLLOWERS: dict[str, keepup.Follower] = {}
_BUILD_LOCK = threading.Lock()


def _age(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - Path(path).stat().st_mtime)
    except OSError:
        return None


def follower(clients_root: str | Path, db_path: str | Path | None = None) -> keepup.Follower:
    """The file's keeper (src/keepup.py): the cases the event ledger names are built again in the background, and every case now and then."""
    clients_root = Path(clients_root)
    path = Path(db_path) if db_path else default_path(clients_root)
    key = str(path)
    if key not in _FOLLOWERS:
        def some(cases: set[str]) -> None:
            with _BUILD_LOCK:
                for case in sorted(cases):
                    if Path(case).name == case and case not in ("", ".", ".."):
                        rebuild(clients_root / case, path)

        def full() -> None:
            with _BUILD_LOCK:
                rebuild_changed(clients_root, path)

        _FOLLOWERS[key] = keepup.Follower(lambda: events.base_path(clients_root.resolve().parent), full, some, name="find index", age=lambda: _age(path))
    return _FOLLOWERS[key]


def keep_up(clients_root: str | Path, db_path: str | Path | None = None) -> bool:
    """What the review app calls before a question. Never waits on the model: with I485_WALK_EVERY above 0 (as installed) the follower brings the
    file up to date in the background; at 0 every case is looked at first. False while there is no file yet (its first build is running)."""
    clients_root = Path(clients_root)
    path = Path(db_path) if db_path else default_path(clients_root)
    if keepup.walk_every() <= 0:
        with _BUILD_LOCK:
            rebuild_changed(clients_root, path)
        return True
    follower(clients_root, path).poke()
    return path.exists()


# -- asking ------------------------------------------------------------------------------------------------------------------------------------

_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}


def _matrix(db: sqlite3.Connection, key: str) -> dict[str, Any]:
    """Every row's vector and what the gate needs, in memory, read again when the file changed (its generation moved)."""
    gen = (db.execute("select value from meta where key = 'generation'").fetchone() or ["0"])[0]
    hit = _CACHE.get(key)
    if hit and hit[0] == gen:
        return hit[1]
    rows = db.execute("select id, case_id, kind, ref, confidential, attorney_only, opened, vector from passages order by id").fetchall()
    dims = len(rows[0]["vector"]) // 4 if rows else 0
    data = {"ids": np.array([r["id"] for r in rows], dtype=np.int64),
            "case": np.array([r["case_id"] for r in rows], dtype=object),
            "kind": np.array([r["kind"] for r in rows], dtype=object),
            "ref": np.array([r["ref"] or "" for r in rows], dtype=object),
            "confidential": np.array([bool(r["confidential"]) for r in rows], dtype=bool),
            "attorney_only": np.array([bool(r["attorney_only"]) for r in rows], dtype=bool),
            "opened": np.array([r["opened"] or "" for r in rows], dtype=object),
            "vectors": np.frombuffer(b"".join(r["vector"] for r in rows), dtype=np.float32).reshape(len(rows), dims) if rows else np.zeros((0, 0), np.float32)}
    _CACHE[key] = (gen, data)
    return data


def why(question: str, passage: str, rec: dict[str, Any]) -> str:
    """One plain line: what the passage is and why it matched, from the words the question and the passage share (never a model's words)."""
    shared = list(dict.fromkeys(w for w in words(question) if w in set(words(passage)) and len(w) > 2))
    where = {"page": f"a page of {_article(rec.get('label') or 'a document')}",
             "translation": f"the office's English translation of {_article(rec.get('label') or 'a document')}",
             "decision": "a review decision", "note": "a case note", "wording": "an approved wording of the firm"}.get(rec["kind"], "the firm's records")
    if shared:
        return f"Close in meaning to your question, in {where}; words in common: {', '.join(shared[:5])}."
    return f"Close in meaning to your question, in {where}; no word in common, so the match is by meaning only."


def _article(label: str) -> str:
    text = str(label or "").strip()
    if not text:
        return "a document"
    if re.match(r"(?i)(a|an|the)\b", text):
        return text
    return ("an " if text[0].lower() in "aeiou" else "a ") + (text[0].lower() + text[1:] if not text[:2].isupper() else text)


def search(question: str, *, db_path: str | Path, model: Embedder | None = None, hidden: Iterable[str] = (), confidential_cases: Iterable[str] | None = None,
           attorney: bool = True, present: Callable[[str], bool] | None = None, may_open: Callable[[str], bool] | None = None,
           wording_ok: Callable[[str], bool] | None = None, limit: int = TOP) -> dict[str, Any]:
    """The passages closest in meaning to the question, for one reader: {"question", "results", "built" (False when there is no file yet)}.

    The gate comes before the ranking: the rows of hidden cases (src/restricted.py scope), of a case whose folder is gone (present(case) False:
    forgotten at once), confidential documents outside confidential_cases (None: every case, the attorney), attorney-only notes unless attorney,
    and wordings wording_ok refuses, are blanked, then the top `limit` above the model's floor are taken, newest case first among equal scores;
    each hit is asked of may_open once more. A result: case, kind, ref, page, file, label, at, text (the verbatim masked passage), score, why."""
    question = " ".join(str(question or "").split())
    out: dict[str, Any] = {"question": question, "results": [], "built": True}
    if not re.search(r"\w", question):
        return out
    path = Path(db_path)
    if not path.exists():
        return out | {"built": False}
    model = model or embedder()
    hidden = set(hidden or ())
    try:
        with closing(connect(path, model)) as db:
            data = _matrix(db, str(path))
            if not len(data["ids"]):
                return out | {"built": db.execute("select count(*) from cases").fetchone()[0] > 0}
            cases = set(data["case"].tolist()) - {""}
            gone = {c for c in cases if present is not None and not present(c)}
            for c in gone:  # a folder taken away: its rows go now, not at the next build
                _delete(db, c)
            q = model.question(question)
            if q.shape[0] != data["vectors"].shape[1]:
                return out | {"built": False}
            scores = data["vectors"] @ q
            allowed = ~np.isin(data["case"], list(hidden | gone)) if hidden or gone else np.ones(len(scores), dtype=bool)
            if confidential_cases is not None:
                allowed &= ~(data["confidential"] & ~np.isin(data["case"], list(set(confidential_cases))))
            if not attorney:
                allowed &= ~data["attorney_only"]
            if wording_ok is not None:
                for i in np.nonzero(allowed & (data["kind"] == "wording"))[0]:
                    allowed[i] = wording_ok(str(data["ref"][i]))
            scores = np.where(allowed, scores, -np.inf)
            # best first; among equal scores (to four places) the newest case first
            opened_rank = np.unique(data["opened"].astype(str), return_inverse=True)[1]
            order = np.lexsort((-data["ids"], -opened_rank, -np.round(scores, 4)))
            picked: list[int] = []
            for i in order:
                if scores[i] < model.floor or len(picked) >= limit:
                    break
                case = str(data["case"][i])
                if case and may_open is not None and not may_open(case):
                    continue
                picked.append(int(i))
            if not picked:
                return out
            ids = [int(data["ids"][i]) for i in picked]
            by_id = {r["id"]: dict(r) for r in db.execute(f"select id, case_id, kind, ref, page, file, label, at, text from passages where id in ({','.join('?' * len(ids))})", ids)}
    except FindDamaged:
        reset(path)
        _CACHE.pop(str(path), None)
        return out | {"built": False}
    for i in picked:
        r = by_id.get(int(data["ids"][i]))
        if r is None:
            continue
        r = {"case": r.pop("case_id")} | r
        r["score"] = round(float(scores[i]), 3)
        r["why"] = why(question, r["text"], r)
        out["results"].append(r)
    return out


# -- who asked what --------------------------------------------------------------------------------------------------------------------------


def questions_path(clients_root: str | Path) -> Path:
    return Path(clients_root).resolve().parent / QUESTIONS


def log_question(clients_root: str | Path, question: str, who: str, role: str | None, hits: int) -> None:
    """One question: a row in the event ledger with who, when and the question's length (never its text: Reports and the query layer read the
    ledger), and a row with the text in find_questions.jsonl (owner-only, appended only), which only an attorney reads."""
    question = " ".join(str(question or "").split())
    events.record("find", "asked", f"Asked Find across the firm a question of {len(question)} characters", home=Path(clients_root).resolve().parent,
                  who=who or None, role=role)
    row = {"at": clock.stamp(), "who": str(who or "").strip() or "Someone not signed in", "role": role, "length": len(question), "question": question, "hits": hits}
    path = questions_path(clients_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as exc:
        import sys

        sys.stderr.write(f"find: the question log was not written ({type(exc).__name__})\n")


def asked(clients_root: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    """The latest questions, newest first, with their text: the attorney's."""
    path = questions_path(clients_root)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return list(reversed(rows))[:limit]


def is_on() -> bool:
    """The attorney's switch (Settings, Drafting and models), off unless switched on."""
    import settings

    return settings.values(SETTING[0]).get(SETTING[1]) == "on"
