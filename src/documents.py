"""The document record: one entry per document the firm holds for a case,
written to data/clients/<id>/documents.json (docs/design_plan.md Part 1).

    {"version": 1, "built": "<ISO time>", "documents": [
      {"id", "files", "doc_ids", "pages", "type", "confidence", "person", "person_set_by", "person_basis",
       "language", "language_basis", "language_country", "issued", "expires", "issued_kind", "expires_kind", "dates_read",
       "identifiers": {"a_number", "receipt", "passport", "ssn_last4"},
       "quality", "quality_set_by", "quality_basis", "quality_measures", "hash", "source", "added", "roles", "found", "tags",
       "confidential", "text", "translated"}]}

"translated" is the English text of a foreign-language document, made once (src/translation.py keeps who made it, the
translator and the signature in translations.json beside this file); null until then.

Every field says where it came from, so the Documents page can say it in words (brief G1, docs/decisions.md 10/03/2026):
  - person_basis: named (the document names the person) | identifiers (it carries a number that is the client's) |
    only_person (it names nobody and the case has one person) | set_by_person | unknown;
  - language_basis: text (its own words) | type (a U.S. government document is in English) | country (the issuing
    country's language, schemas/law/country_languages.json; language_country names the country) | set_by_person | unknown;
  - issued_kind / expires_kind: what each date is (issued, registered, arrived, notice, order / expires, admit_until,
    valid_to); dates_read: a reader ran on it (False: "not read yet", True with no dates: "none found on it");
  - quality_basis: measured (measure_quality: the page's picture and the words read) | reader (the classifier knew it) |
    retake (the portal asked for another photo) | set_by_person | unknown; quality_measures keeps the numbers and the
    words for the screen;
  - found: roles the document's own text shows beyond its type ({"entry": "a U.S. admission stamp"} in a passport).

Nothing here runs a reader again: every field comes from what the
pipeline already holds when a client is processed -- the classifier's type
and confidence, the split of a combined scan into its parts, the
extractors' fields, the text they read (batch.process_client_folder and
portal/engine.process_client call build() once, right after classification
and extraction). Two things look at the file itself: measure_quality (the
page's picture), and a case processed before documents.json existed, whose
records are built from its fact graph's sources and the file's own text layer
(load). The taxonomy (schemas/registers/document_types.json) says what each
type is called on screen, the exhibit roles it fills, whether it expires,
whether it is confidential, and whether it is read beyond its type (an
engagement letter, attorney correspondence, the sealed I-693 envelope:
their text is never kept).

  - id: the first 16 hex of the file's sha256; a part of a combined scan
    ("file.pdf#p3-4") gets the first 16 hex of sha256("<file hash>#p3-4"),
    so each part is its own record that knows its pages;
  - the same file uploaded twice (same hash) is one record with two files
    (packet.plan puts it in the packet once);
  - person: who the document is about, where the document itself says it
    (the reader names the person: a passport's MRZ, a birth certificate's
    registrant and parents, a petitioner's card); else the client when its
    number is the client's or the case has nobody else (infer_people, on
    every load); otherwise "unknown" until a reviewer sets it (set_person);
  - a reviewer's person, language, quality, dates and role tags (with who and
    when) survive every reprocessing (merge).

doc_ids lists the names the rest of the pipeline knows the document by
(meta.json's classifications, the fact graph's sources): the file name, or
"file.pdf#p3-4" for a part. quality_set_by marks a quality a reviewer set; dates_set_by the issued and
end dates a reviewer read off the document (set_dates).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import clock
import events
import schema_path

FILE = "documents.json"
VERSION = 1
QUALITIES = ("readable", "blurry", "cut_off", "partial", "check", "unknown")  # check: measure_quality's "needs a check"
LANGUAGES = ("en", "pt", "es", "fr", "ht", "unknown")  # what a reviewer can set (set_language); language() reads pt, es, ht, en
PEOPLE = ("applicant", "spouse", "petitioner", "parent", "unknown")  # + child_1, child_2, ...
SOURCES = ("portal", "scan_inbox", "drive", "m365", "filevine", "docketwise", "clio", "folder")
SOURCE_WORDS = {"portal": "the client portal", "scan_inbox": "the notice inbox", "drive": "Google Drive", "m365": "Microsoft 365", "filevine": "Filevine",
                "docketwise": "Docketwise", "clio": "Clio", "folder": "a scan the office added"}  # where a document came from, in words (the ledger's sentence)
# connectors/sync.py names a mirrored client's folder "<name>-<prefix><id>"; tools/import_docketwise.py names its folders the same way
_CONNECTOR_PREFIX = {"fv": "filevine", "gd": "drive", "ms": "m365", "dw": "docketwise", "cl": "clio"}


def _now() -> str:
    return clock.stamp()


# --- the taxonomy ------------------------------------------------------------


def types() -> dict[str, dict[str, Any]]:
    from classify.patterns import TYPES

    return TYPES


def roles() -> list[str]:
    from classify.patterns import TAXONOMY_PATH

    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))["roles"]


def type_info(doc_type: str) -> dict[str, Any]:
    """The taxonomy's entry; a type it doesn't know reads as unclassified (nothing confidential, a reviewer says whose)."""
    return types().get(doc_type) or {**types()["unclassified"], "id": doc_type}


def name(doc_type: str) -> str:
    from classify.patterns import name as type_name

    return type_name(doc_type)


def short_name(doc_type: str) -> str:
    """The name for a column on a staff screen (the long name stays on hover and in the bundle): schemas/registers/document_types.json "short_name" when the type has one."""
    from classify.patterns import short_name as short

    return short(doc_type)


def valid_person(person: str) -> bool:
    return person in PEOPLE or bool(re.fullmatch(r"child_[1-9]\d?", person or ""))


# --- a confidential case -------------------------------------------------------------
# A type's own flag (schemas/registers/document_types.json) covers a document that arrives before the case has a
# filing; once the case is a protected one, every document in it is protected, whatever its type:
#   8 U.S.C. 1367(a)(2) (govinfo, read 10/02/2026): no disclosure of "any information which relates to an
#     alien who is the beneficiary of an application for relief under paragraph (15)(T), (15)(U), or (51)
#     of section 101(a)" -- the T visa, the U visa, the VAWA self-petitioner;
#   8 CFR 208.6 (eCFR, read 10/02/2026): (a) information "contained in or pertaining to any application for
#     refugee admission, asylum, withholding of removal ..., or protection under ... the Convention Against
#     Torture"; (b) other records "that indicate that a specific alien has applied for refugee admission,
#     asylum" (an I-730 relative petition says the client was granted asylum or refugee status).
# Tracks as src/journey.py decides them; filings as packet.FILINGS names them (status.json's mailings, the
# packet last built): the VAWA I-360, the I-914 (with its Supplements and the I-192 inside it), the U
# certification and petition (I-918, its Supplements, the I-192); the I-589, the asylee's green card, the I-730.
_TRACKS = {"vawa": "1367", "t_visa": "1367", "u_visa": "1367", "asylum": "208.6"}
_FILINGS = {"vawa": "1367", "i914": "1367", "i914b": "1367", "u_visa": "1367", "u_cert": "1367",
            "i589": "208.6", "asylee": "208.6", "i730": "208.6"}


def case_confidentiality(client_dir: str | Path, graph=None) -> str | None:
    """"1367", "208.6" or None for the whole case: its track (a person's choice on the case page first, else
    src/journey.py track_of), then any filing mailed or packet built. 1367 wins when both apply."""
    client_dir = Path(client_dir)
    found: set[str] = set()
    status_path = client_dir / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    filings = {r.get("filing") for r in status.get("filings") or []}
    try:
        import journey

        filings.add(journey._latest_packet(client_dir).get("filing"))
        track = ((status.get("journey") or {}).get("track") or {}).get("value")
        if not track and (graph is not None or (client_dir / "fact_graph.json").exists()):
            if graph is None:
                from review.state import reviewed_graph

                graph = reviewed_graph(client_dir)
            track = journey.track_of(graph, journey.notices(graph), journey._docs(client_dir), journey._latest_packet(client_dir).get("filing"))
    except Exception:  # noqa: BLE001 -- a case that can't be read yet keeps its filings' and its types' flags
        track = ((status.get("journey") or {}).get("track") or {}).get("value")
    found |= {_TRACKS[track]} if track in _TRACKS else set()
    found |= {_FILINGS[f] for f in filings if f in _FILINGS}
    return "1367" if "1367" in found else "208.6" if found else None


def with_case_confidentiality(client_dir: str | Path, data: dict[str, Any], graph=None) -> dict[str, Any]:
    """Every record's confidential: the case's flag when it is a protected case, else the type's own."""
    case = case_confidentiality(client_dir, graph)
    for r in data.get("documents") or []:
        own = type_info(r.get("type", "unclassified")).get("confidential")
        r["confidential"] = "1367" if "1367" in (case, own) else case or own
    return data


# --- reading what the pipeline already holds ----------------------------------

_PARTICLES = {"DE", "DA", "DO", "DOS", "DAS", "DEL", "LA", "Y", "E", "VAN", "DER"}


def _words(text: Any) -> list[str]:
    """Upper case, accents removed, particles (DE, DA, DOS...) dropped: "João da Silva" -> ["JOAO", "SILVA"]."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", str(text or "").upper()) if not unicodedata.combining(c))
    return [w for w in re.findall(r"[A-Z]{2,}", folded) if w not in _PARTICLES]


def _tokens(text: Any) -> set[str]:
    return set(_words(text))


def _split_full(full: str) -> tuple[set[str], set[str]]:
    """'ANA CLARA EXEMPLO SOUZA' -> ({ANA}, {CLARA, EXEMPLO, SOUZA}): the first word is a given name, the rest may be family names."""
    words = _words(full)
    return (set(words[:1]), set(words[1:])) if len(words) > 1 else (set(), set())


def _same_person(name: tuple[set[str], set[str]], person: tuple[set[str], set[str]] | None) -> bool:
    """A given name and a family name in common: a child shares the family names, never (in practice) the given name too."""
    return bool(person and name[0] & person[0] and name[1] & person[1])


def _case_people(graph, doc_id: str) -> dict[str, Any]:
    """The client's, the spouse's and the parents' names as the rest of the case states them (never from doc_id itself):
    the client's own answers first, other documents otherwise."""

    def sources(key: str) -> list:
        fact = graph.get(key) if graph is not None else None
        found = [s for s in (fact.sources if fact is not None else []) if s.doc_id != doc_id]
        stated = [s for s in found if s.doc_type == "intake_questionnaire"]
        return stated or found

    def pair(given_key: str, family_key: str) -> tuple[set[str], set[str]] | None:
        given = set().union(*[_tokens(s.normalized_value) for s in sources(given_key)])
        family = set().union(*[_tokens(s.normalized_value) for s in sources(family_key)])
        return (given, family) if given and family else None

    def one(key: str) -> str | None:
        """The name the case settles on: resolved in the graph; in a conflict (a child's I-94 next to the
        client's), the client's own answer, else the value most sources give."""
        fact = graph.get(key) if graph is not None else None
        if fact is None or not fact.sources:
            return None
        if fact.status == "resolved" and fact.value:
            return str(fact.value)
        stated = {str(s.normalized_value) for s in fact.sources if s.doc_type == "intake_questionnaire" and s.normalized_value}
        if len(stated) == 1:
            return stated.pop()
        counts: dict[str, int] = {}
        for s in fact.sources:
            if s.normalized_value:
                counts[str(s.normalized_value)] = counts.get(str(s.normalized_value), 0) + 1
        ranked = sorted(counts.values(), reverse=True)
        return max(counts, key=counts.get) if ranked and (len(ranked) == 1 or ranked[0] > ranked[1]) else None

    def client() -> tuple[set[str], set[str]] | None:
        given, family = one("applicant.given_name"), one("applicant.family_name")
        if given and family:
            return _tokens(given), _tokens(family)
        return pair("applicant.given_name", "applicant.family_name")  # no name the case settles on: every other source's

    parents = [p for p in (pair("applicant.father_given_name", "applicant.father_family_name"),
                           pair("applicant.mother_given_name", "applicant.mother_family_name")) if p]
    for key in ("questionnaire.father_name", "questionnaire.mother_name"):
        parents += [_split_full(s.normalized_value) for s in sources(key)]
    return {"applicant": client(),
            "spouse": pair("applicant.spouse_given_name", "applicant.spouse_family_name"),
            "parents": [p for p in parents if p[0] and p[1]]}


def _case_value(graph, key: str, doc_id: str) -> str | None:
    """A fact as the rest of the case states it (never from doc_id itself): the value the graph settled on, else the client's
    own answer, else the one value every other source gives."""
    fact = graph.get(key) if graph is not None else None
    if fact is None:
        return None
    others = [s for s in fact.sources if s.doc_id != doc_id and s.normalized_value not in (None, "")]
    if fact.status == "resolved" and fact.value and (others or not fact.sources):
        return str(fact.value)
    stated = {str(s.normalized_value) for s in others if s.doc_type == "intake_questionnaire"}
    values = stated or {str(s.normalized_value) for s in others}
    return values.pop() if len(values) == 1 else None


def _value(fields: list, key: str) -> Any:
    return next((f.normalized_value for f in fields if f.fact_key == key and f.normalized_value not in (None, "")), None)


def _named(doc_type: str, fields: list, text: str) -> tuple[set[str], set[str]] | None:
    """Whose name the document carries, as its reader found it."""
    if doc_type == "passport":
        from extract.passport import mrz_name

        found = mrz_name(text)
        return (_tokens(found[1]), _tokens(found[0])) if found else None
    if doc_type == "birth_certificate":
        full = _value(fields, "applicant.birth_certificate_name")
        return _split_full(full) if full else None
    given, family = _value(fields, "applicant.given_name"), _value(fields, "applicant.family_name")
    return (_tokens(given), _tokens(family)) if given and family else None


def _person(doc_type: str, fields: list, text: str, people: dict[str, Any], children: dict[frozenset, str]) -> tuple[str, bool]:
    """(who the document is about, whether it names anyone): the person when the document says so (the taxonomy's person_from
    "document"); "unknown" otherwise -- and "unknown" with a name found when the name is nobody the case knows."""
    if type_info(doc_type).get("person_from") != "document":
        return "unknown", False
    if any(f.fact_key.startswith("petitioner.") for f in fields) or doc_type in ("us_passport", "citizenship_certificate", "green_card",
                                                                                  "us_birth_certificate"):
        return "petitioner", True  # extract/petitioner_docs.py: the relative's, never the client's
    me = people.get("applicant")
    if doc_type == "marriage_certificate":
        parties = [_split_full(f.normalized_value) for f in fields if re.fullmatch(r"marriage\.[a-z_]+\.name", f.fact_key)]
        mine = any(_same_person(p, me) for p in parties) or (bool(fields) and me is None)
        return ("applicant" if mine else "unknown"), bool(parties) or mine
    named = _named(doc_type, fields, text)
    if named is None or not (named[0] and named[1]):
        return "unknown", False
    if me is None or _same_person(named, me):
        return "applicant", True  # the reader filed its facts as the client's, and nothing in the case says otherwise
    if doc_type == "birth_certificate":
        parents = [_split_full(f.normalized_value) for f in fields if re.fullmatch(r"applicant\.birth_cert\.[a-z_]+_name", f.fact_key)]
        if any(_same_person(p, me) for p in parents):  # the client is a parent on it: the client's child
            key = frozenset(named[0] | named[1])
            return children.setdefault(key, f"child_{len(children) + 1}"), True
    if _same_person(named, people.get("spouse")):
        return "spouse", True
    if any(_same_person(named, p) for p in people.get("parents", [])):
        return "parent", True
    return "unknown", True


# Words only one language prints on a document (accents removed; words two languages share are left out).
_LANGUAGE_WORDS = {
    "pt": {"CERTIDAO", "NASCIMENTO", "FEDERATIVA", "BRASIL", "SOBRENOME", "NOME", "FILIACAO", "DATA", "NACIONALIDADE", "PASSAPORTE",
           "CARTORIO", "NATURALIDADE", "AVOS", "PAI", "MAE", "CASAMENTO", "CIDADE", "EXPEDICAO", "VALIDADE", "ENDERECO", "NAO", "VOCE",
           "SIM", "FILHO", "FILHA", "COM", "DO", "DA", "DOS", "DAS", "UMA", "MATRICULA", "REGISTRADO", "SAO"},
    "es": {"NACIMIENTO", "NOMBRE", "NOMBRES", "APELLIDOS", "FECHA", "LUGAR", "ACTA", "PADRE", "MADRE", "MATRIMONIO", "NACIONALIDAD",
           "PASAPORTE", "CIUDAD", "DEPARTAMENTO", "EL", "LOS", "LAS", "DEL", "CON", "UNA", "CEDULA", "IDENTIDAD", "PARTIDA",
           "REGISTRADOR", "DATOS", "INSCRIPCION", "SENOR", "USTED"},
    "ht": {"MWEN", "NOU", "YO", "NAN", "AK", "POU", "KI", "GEN", "TANPRI", "SIYATI", "PAPA", "MANMAN", "NESANS", "AYITI", "REPIBLIK",
           "ADRES", "KREYOL", "PEYI", "SOU", "PITIT", "FANMI", "LEKOL", "TRAVAY"},
    "en": {"THE", "OF", "AND", "DATE", "BIRTH", "NAME", "NAMES", "CERTIFICATE", "UNITED", "STATES", "NUMBER", "RECEIPT", "NOTICE",
           "ADDRESS", "SURNAME", "GIVEN", "NATIONALITY", "PASSPORT", "ISSUED", "EXPIRES", "FOR", "THIS", "WITH", "YOUR", "SOCIAL",
           "SECURITY", "DEPARTMENT", "HOMELAND", "ADMISSION", "RECORD", "FIRST", "LAST", "COUNTRY", "CITIZENSHIP", "UNTIL", "PLACE",
           "APPROVAL", "TYPE", "CASE", "COURT", "ORDER", "STATEMENT", "PAY", "TOTAL", "ACCOUNT"},
}


# Words that are also parts of people's and places' names ("JOAO DA SILVA DOS SANTOS", "SAO PAULO", "MARIA DEL CARMEN DE LOS
# SANTOS"): an English document full of Portuguese names is still English (brief G1 verification, 10/03/2026).
_NAME_PARTICLES = {"pt": {"DO", "DA", "DOS", "DAS", "SAO"}, "es": {"DEL", "LOS", "LAS", "EL"}}


def language(text: str) -> str:
    """pt, es, ht or en -- a document printed in two languages is the foreign one (it needs a certified translation:
    8 CFR 103.2(b)(3)); "unknown" when the text says too little."""
    words = set(re.findall(r"[A-Z]+", "".join(c for c in unicodedata.normalize("NFKD", (text or "").upper()) if not unicodedata.combining(c))))
    scores = {}
    for lang, markers in _LANGUAGE_WORDS.items():  # a name's particles count only as far as the language's other words back them
        particles = len(words & markers & _NAME_PARTICLES.get(lang, set()))
        strong = len(words & (markers - _NAME_PARTICLES.get(lang, set())))
        scores[lang] = strong + min(particles, strong)
    foreign = sorted((s, lang) for lang, s in scores.items() if lang != "en")
    (best, lang), (second, _) = foreign[-1], foreign[-2]
    if best >= 3 and best > 1.5 * second:
        return lang
    return "en" if scores["en"] >= 3 and best < 3 else "unknown"


def _fold(text: Any) -> str:
    """Upper case, accents removed: "Emissão" -> "EMISSAO"."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text or "").upper()) if not unicodedata.combining(c))


COUNTRY_LANGUAGES = schema_path.path("law", "country_languages")
_COUNTRIES: dict[str, dict[str, Any]] = {}


def countries() -> dict[str, dict[str, Any]]:
    """ISO alpha-3 -> {name, names, language}: the language a country's civil documents are printed in (general knowledge,
    schemas/law/country_languages.json; shown on screen as an assumption a person can correct)."""
    if not _COUNTRIES:
        _COUNTRIES.update(json.loads(COUNTRY_LANGUAGES.read_text(encoding="utf-8"))["countries"])
    return _COUNTRIES


def country_of(value: Any) -> str | None:
    """"BRA", "BRAZIL", "Brasil" -> "BRA"; None for a country the table doesn't know."""
    folded = re.sub(r"\s+", " ", _fold(value)).strip()
    if folded in countries():
        return folded
    return next((code for code, c in countries().items() if folded in c["names"]), None)


def _country_in_text(text: str) -> str | None:
    """The country a certificate names in its heading ("REPUBLICA FEDERATIVA DO BRASIL"): the first six lines only, where the
    issuing country is printed (a place of birth further down may be anywhere)."""
    head = _fold("\n".join((text or "").splitlines()[:6]))
    found = [(m.start(), code) for code, c in countries().items() for n in c["names"] for m in re.finditer(rf"(?<![A-Z]){re.escape(n)}(?![A-Z])", head)]
    return min(found)[1] if found else None


def document_language(doc_type: str, text: str, fields: list, case_country: str | None = None) -> tuple[str, str, str | None]:
    """(language, basis, the country assumed) for one document:
      - a U.S. government document (the taxonomy's "language": "en") is in English, whatever names are printed on it;
      - otherwise its own words (language(): the marker words of one language);
      - otherwise, for a passport, a birth or marriage certificate or a national identity card (the taxonomy's "language_by":
        "country"), the language of the country that issued it: a passport's MRZ issuing state, a certificate's country on
        it, else case_country (the client's country of birth or citizenship, from the fact graph) -- an assumption, shown as one;
      - otherwise "unknown". A document never read beyond its type (the sealed envelope, privileged letters) stays "unknown"."""
    info = type_info(doc_type)
    if not info.get("read_beyond_type", True):
        return "unknown", "unknown", None
    if info.get("language"):
        return info["language"], "type", None
    found = language(text)
    if found != "unknown":
        return found, "text", None
    if info.get("language_by") == "country":
        if doc_type == "passport":
            from extract.passport import read_mrz

            mrz = read_mrz(text or "") if text else None
            named = [mrz["issuer"]] if mrz and mrz.get("issuer") else []
            named += [str(f.normalized_value).split("|", 1)[0] for f in fields if f.fact_key.startswith("folder.passport.")]
        else:
            named = [f.normalized_value for f in fields if f.fact_key == "applicant.country_of_birth" and f.normalized_value]
        on_it = [country_of(n) for n in named]
        code = next((c for c in on_it if c), None) or (None if named else _country_in_text(text))
        # the client's country only when the document names none: a passport the MRZ says China issued is never "assumed: Brazil"
        code = code or (None if named else country_of(case_country))
        if code and countries()[code].get("language"):
            return countries()[code]["language"], "country", countries()[code]["name"]
    return "unknown", "unknown", None


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso(v: Any) -> str | None:
    m = _ISO.fullmatch(str(v or "").strip())
    return m.group(0) if m else None


# The labels beside a document's dates (accents removed, upper case), and the month names they are written with.
_ISSUE_LABELS = ("DATE OF ISSUE", "ISSUE DATE", "DATE ISSUED", "ISSUED ON", "DATA DE EMISSAO", "DATA DA EMISSAO", "DATA EMISSAO",
                 "DATA DE EXPEDICAO", "DATA DA EXPEDICAO", "DATA EXPEDICAO", "FECHA DE EXPEDICION", "FECHA DE EMISION", "FECHA EXPEDICION",
                 "DELIVRE LE", "DATE DE DELIVRANCE")
_REGISTERED_LABELS = ("DATA DO REGISTRO", "DATA DE REGISTRO", "DATA DO REGISTO", "DATA DO ASSENTO", "FECHA DE REGISTRO",
                      "FECHA DE INSCRIPCION", "FECHA DE ASENTAMIENTO", "DATE OF REGISTRATION", "REGISTRATION DATE", "DATE D'ENREGISTREMENT",
                      "ENREGISTRE LE")
_MONTHS = {m: n for n, names in enumerate((
    "JAN JANUARY JANEIRO ENERO JANVIER", "FEB FEV FEVEREIRO FEBRUARY FEBRERO FEVRIER", "MAR MARCH MARCO MARZO MARS",
    "APR ABR APRIL ABRIL AVR AVRIL", "MAY MAI MAIO MAYO", "JUN JUNE JUNHO JUNIO JUIN", "JUL JULY JULHO JULIO JUIL JUILLET",
    "AUG AGO AUGUST AGOSTO AOU AOUT", "SEP SEPT SET SEPTEMBER SETEMBRO SEPTIEMBRE SETIEMBRE SEPTEMBRE",
    "OCT OUT OCTOBER OUTUBRO OCTUBRE OCTOBRE", "NOV NOVEMBER NOVEMBRO NOVIEMBRE NOVEMBRE", "DEC DEZ DIC DECEMBER DEZEMBRO DICIEMBRE DECEMBRE",
), start=1) for m in names.split()}
_WORD_DATE = re.compile(r"\b(\d{1,2})\s*(?:DE\s+)?([A-Z]{3,10})(?:\s*/\s*[A-Z]{3,10})?\s*(?:DE\s+)?(\d{4})\b")
_NUMBER_DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# The kinds of document whose dates are read off their labels: foreign civil documents, which no field reader dates.
_LABELLED = ("passport", "birth_certificate", "marriage_certificate", "divorce_decree", "national_id", "police_clearance")


def _a_date(year: int, month: int, day: int) -> str | None:
    try:
        when = datetime(year, month, day).date()
    except ValueError:
        return None
    return when.isoformat() if 1900 <= year and when <= clock.today() else None  # a document is never issued in the future


def _date_after(window: str, day_first: bool) -> str | None:
    """The first date written in the window: "11 JAN/JAN 2022", "10 de janeiro de 2022", "10/01/2022" (day first unless the
    document is in English), "2022-01-10"."""
    hits = []
    for m in _WORD_DATE.finditer(window):
        if m.group(2) in _MONTHS:
            hits.append((m.start(), _a_date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))))
    for m in _NUMBER_DATE.finditer(window):
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hits.append((m.start(), _a_date(year, b, a) if day_first else _a_date(year, a, b)))
    for m in _ISO_DATE.finditer(window):
        hits.append((m.start(), _a_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    hits = sorted(h for h in hits if h[1])
    return hits[0][1] if hits else None


def _labelled_date(text: str, labels: Iterable[str], day_first: bool) -> str | None:
    """The date printed after one of these labels: on the label's own line or the line under it (a bilingual label
    "DATA DE EXPEDICAO / DATE OF ISSUE" sits above its date)."""
    folded = _fold(text)
    for label in labels:
        for m in re.finditer(rf"(?<![A-Z]){re.escape(label)}(?![A-Z])", folded):
            rest = folded[m.end():]
            window = "\n".join(rest.split("\n")[:2])[:120]
            found = _date_after(window, day_first)
            if found:
                return found
    return None


def read_dates(doc_type: str, fields: list, text: str = "") -> dict[str, Any]:
    """{issued, expires, issued_kind, expires_kind} from the readers' own fields: a notice's date and valid-to (the notice reader's
    folder.notice.* fields, else the date its folder.uscis_case line ends with), a passport's MRZ expiry (its field, else the MRZ
    in the text), a work permit's end date, an I-94's arrival and admit-until, a certificate's or a court order's date; and for a
    foreign civil document (_LABELLED) the issue date printed beside its label ("Date of issue", "Data de emissão", "Data de
    expedição", "Fecha de expedición", "Délivré le"), else its registration date ("Data do registro", "Fecha de inscripción")."""
    out: dict[str, Any] = {"issued": None, "expires": None, "issued_kind": None, "expires_kind": None}

    def put(which: str, value: str | None, kind: str) -> None:
        if value and not out[which]:
            out[which], out[which + "_kind"] = value, kind

    notice_dates = []
    for f in fields:
        key, v = f.fact_key, f.normalized_value
        if key.startswith("folder.passport.") and "|" in str(v):
            put("expires", _iso(str(v).split("|", 1)[1]), "expires")
        elif key == "applicant.ead_expiration_date":
            put("expires", _iso(v), "expires")
        elif key == "applicant.i94_admit_until_date":
            put("expires", _iso(v), "admit_until")
        elif re.fullmatch(r"folder\.notice\..+\.valid_to", key):
            put("expires", _iso(v), "valid_to")
        elif key == "applicant.i94_arrival_date":
            put("issued", _iso(v), "arrived")
        elif key == "sij.order_date":
            put("issued", _iso(v), "order")
        elif key == "petitioner.certificate_date":
            put("issued", _iso(v), "issued")
        elif re.fullmatch(r"folder\.notice\..+\.date", key):
            put("issued", _iso(v), "notice")
        elif key.startswith("folder.uscis_case."):  # "I-360 APPROVAL (Special Immigrant Juvenile), 2025-08-20" (extract/uscis_notice.py describe)
            m = re.search(r",\s*(\d{4}-\d{2}-\d{2})\s*$", str(v or ""))
            notice_dates.append(m.group(1) if m else None)
    for when in notice_dates:
        put("issued", _iso(when), "notice")
    if doc_type == "passport" and not out["expires"] and text:
        from extract.passport import read_mrz

        mrz = read_mrz(text)
        put("expires", mrz and mrz.get("expiry"), "expires")
    if doc_type in _LABELLED and text:
        day_first = language(text) != "en"  # a foreign document writes the day first; an English one may not
        put("issued", _labelled_date(text, _ISSUE_LABELS, day_first), "issued")
        put("issued", _labelled_date(text, _REGISTERED_LABELS, day_first), "registered")
    return out


def dates(fields: list, doc_type: str = "", text: str = "") -> tuple[str | None, str | None]:
    """(issued, expires): read_dates without the kinds."""
    found = read_dates(doc_type, fields, text)
    return found["issued"], found["expires"]


# What a passport shows beyond identity and nationality: an entry, only when the text has the proof of one
# (brief G1: a passport is not itself proof of entry; an admission stamp or a U.S. visa in it is).
_US_VISA = re.compile(r"^V[<A-Z]USA", re.M)
_ADMISSION = re.compile(r"(?<![A-Z])(?:ADMITTED|PAROLED|CBP|CUSTOMS AND BORDER PROTECTION|U\.?\s?S\.?\s+IMMIGRATION|ADMIT UNTIL|ADMITTED UNTIL)(?![A-Z])")
_CLASS_AND_DATE = re.compile(r"(?<![A-Z])CLASS(?: OF ADMISSION)?\s*:?\s*[A-Z]{1,2}-?\d{0,2}(?![A-Z0-9])[^\n]{0,40}?\b\d{1,2}[ ./-]?(?:[A-Z]{3}|\d{1,2})[ ./-]?\d{2,4}\b")


def found_roles(doc_type: str, text: str) -> dict[str, str]:
    """{role: what was found} that the document's own text shows beyond its type's roles: a passport shows an entry to the
    U.S. when a U.S. visa (its machine-readable "V<USA" line, or the visa foil's own headings) or an admission stamp
    ("ADMITTED", "PAROLED", "CBP", "U.S. IMMIGRATION", a class of admission with a date) is found in it."""
    if doc_type != "passport" or not text:
        return {}
    folded = _fold(text)
    foil = "UNITED STATES OF AMERICA" in folded and re.search(r"(?<![A-Z])VISA(?![A-Z])", folded) and re.search(r"CONTROL\s+(?:NUMBER|NO)|ANNOTATION|VISA\s+TYPE", folded)
    if _US_VISA.search(folded) or foil:
        return {"entry": "a U.S. visa"}
    if _ADMISSION.search(folded) or _CLASS_AND_DATE.search(folded):
        return {"entry": "a U.S. admission stamp"}
    return {}


def identifiers(fields: list) -> dict[str, str]:
    out = {"a_number": "", "receipt": "", "passport": "", "ssn_last4": ""}
    for f in fields:
        key, v = f.fact_key, str(f.normalized_value or "")
        if key in ("applicant.a_number", "petitioner.a_number") and not out["a_number"]:
            out["a_number"] = v
        elif key == "applicant.i360_receipt_number" and not out["receipt"]:
            out["receipt"] = v
        elif key.startswith("folder.uscis_case.") and not out["receipt"]:
            out["receipt"] = str(f.raw_value or "")
        elif key.startswith("folder.passport.") and not out["passport"]:
            out["passport"] = key.split(".", 2)[2]
        elif key == "applicant.travel_document_number" and not out["passport"]:
            out["passport"] = v
        elif key == "applicant.ssn" and len(re.sub(r"\D", "", v)) >= 4:
            out["ssn_last4"] = re.sub(r"\D", "", v)[-4:]
    return out


def quality(doc_type: str, text: str, retake: bool = False) -> str:
    """The rule before measure_quality, and its fallback when the file can't be looked at: readable (the classifier knew it),
    blurry (its text reads as nonsense, or the portal asked for a retake), unknown otherwise. cut_off and partial are a
    reviewer's call (set_quality)."""
    from classify.classifier import readability

    if retake:
        return "blurry"
    if not (text or "").strip():
        return "unknown"
    if doc_type not in ("unclassified",):
        return "readable"
    return "blurry" if readability(text) < 3 else "unknown"


# --- how readable a page is, measured (brief G1; thresholds are a first estimate: docs/decisions.md 10/03/2026) ------------
# Each page is looked at 1,000 pixels wide (a letter page at about 120 dots per inch), in grey:
#   sharpness: the variance of its edge image (PIL ImageFilter.FIND_EDGES) divided by the share of the page that is ink, in
#     thousands -- so a page with little printed on it is not called blurry for having few edges. Rendered text pages measure
#     about 75; the same page under a 1-pixel blur about 11, 1.5 pixels about 1.5, 2 pixels about 0.3.
#   contrast: the mean grey of the paper minus the mean grey of the ink (split by Otsu's threshold), 0 to 255.
#   skew: the angle (to half a degree, up to MAX_SKEW either way) at which the ink's rows line up best (projection profile).
#   A sharp card photographed on a dark table can read as faint or soft (the table is part of the picture): "Needs a check".
#   words: words of two letters or more in the text the pipeline read; confidence: Tesseract's mean word confidence, asked
#     only of a scanned page (no text layer of its own) the pipeline read fewer than 30 words from (classify/ocr.py ocr_words):
#     the batch already OCRs every scan once, so a second pass is spent only where the words read leave the question open.
SHARP, SOFT = 5.0, 1.0          # at or above SHARP: sharp; below SOFT: blurry; between: a little soft (needs a check)
CONTRAST, FAINT = 60, 25        # at or above CONTRAST: good contrast; below FAINT: very faint (hard to read); between: faint
TILTED = 5                      # degrees: a page tilted this much or more needs a check
MAX_SKEW = 15                   # degrees: the tilt is looked for this far either way; more reads "15 degrees or more"
FEW_WORDS = 5                   # fewer words read than this, on a document that should have words: needs a check
CONFIDENT, UNSURE = 70, 50      # Tesseract's mean confidence: below UNSURE hard to read; between: needs a check
_WIDTH = 1000
_MEASURED: dict[tuple, dict[str, Any]] = {}  # (file, its mtime and size, page) -> the picture's measures: a file is looked at once per process


def _otsu(histogram: list[int]) -> int:
    total, weighted = sum(histogram), sum(i * h for i, h in enumerate(histogram))
    below = below_sum = 0
    best, threshold = -1.0, 127
    for i, h in enumerate(histogram):
        below += h
        if not below:
            continue
        above = total - below
        if not above:
            break
        below_sum += i * h
        between = below * above * (below_sum / below - (weighted - below_sum) / above) ** 2
        if between > best:
            best, threshold = between, i
    return threshold


def _picture(image) -> dict[str, Any]:
    """The measures of one page's picture (a PIL image)."""
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps

    grey = ImageOps.grayscale(image)
    if grey.width != _WIDTH:
        grey = grey.resize((_WIDTH, max(1, round(grey.height * _WIDTH / grey.width))))
    pixels = np.asarray(grey, dtype=float)
    spread = float(np.percentile(pixels, 99.9) - np.percentile(pixels, 0.1))
    ink = pixels <= _otsu(grey.histogram())
    if ink.mean() > 0.5:  # light print on a dark ground (a card photographed on a dark table): the ink is the smaller part
        ink = ~ink
    share = float(ink.mean())
    if spread < 20 or share < 0.0005:
        return {"blank": True, "sharpness": 0.0, "contrast": round(spread), "skew": 0.0}
    contrast = abs(float(pixels[~ink].mean() - pixels[ink].mean()))
    edges = np.asarray(grey.filter(ImageFilter.FIND_EDGES), dtype=float)[1:-1, 1:-1]
    sharpness = float(edges.var()) / share / 1000
    mask = Image.fromarray((ink * 255).astype("uint8"))
    small = mask.resize((500, max(1, round(mask.height * 500 / mask.width))))
    def lined_up(degrees: float) -> float:
        return float(np.asarray(small.rotate(degrees, resample=Image.BILINEAR, fillcolor=0), dtype=float).sum(axis=1).var())

    best, turn = lined_up(0.0), 0.0  # whole degrees out to MAX_SKEW, then half a degree either side of the best
    for degrees in (d for d in range(-MAX_SKEW, MAX_SKEW + 1) if d):
        score = lined_up(float(degrees))
        if score > best + 1e-6:
            best, turn = score, float(degrees)
    for degrees in (turn - 0.5, turn + 0.5):
        if abs(degrees) <= MAX_SKEW:
            score = lined_up(degrees)
            if score > best + 1e-6:
                best, turn = score, degrees
    angle = -turn
    return {"blank": False, "sharpness": round(sharpness, 2), "contrast": round(contrast), "skew": angle}


def _page_pictures(source, pages: Iterable[int] | None) -> list[tuple[int, Any]]:
    """[(page number, PIL image)] for up to three of the document's pages: a PDF's pages rendered with PDFium (the same lock as
    the questionnaire reader: PDFium is not thread-safe), a photo as it is, an image passed in as it is."""
    if not isinstance(source, (str, Path)):
        return [(1, source)]
    path = Path(source)
    wanted = list(pages or [1])[:3]
    if path.suffix.lower() != ".pdf":
        from PIL import Image

        with Image.open(path) as image:
            image.load()
            return [(1, image.copy())]
    import pypdfium2 as pdfium

    from questionnaire.pages import PDFIUM_LOCK

    out = []
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(str(path))
        try:
            for number in wanted:
                if 1 <= number <= len(document):
                    page = document[number - 1]
                    out.append((number, page.render(scale=_WIDTH / page.get_width()).to_pil().convert("L")))  # copied out before the close
                    page.close()
        finally:
            document.close()
    return out


def _words_in(text: str) -> int:
    return len(re.findall(r"[^\W\d_]{2,}", text or ""))


def _ocr_confidence(image) -> float | None:
    """Tesseract's mean confidence over the words it reads on the page (classify/ocr.py), None when it isn't installed."""
    from classify.ocr import find_tesseract, ocr_words

    if find_tesseract() is None:
        return None
    try:
        confident = [w.conf for w in ocr_words(image) if w.conf >= 0]
    except Exception:  # noqa: BLE001 -- the estimate goes without it
        return None
    return round(sum(confident) / len(confident)) if confident else 0.0


def measure_quality(source, pages: Iterable[int] | None = None, *, text: str | None = None, expects_text: bool = True,
                    ocr: bool = False) -> dict[str, Any] | None:
    """How readable a document is, estimated from its own pages (a PDF's or a photo's path, or a PIL image) and the words the
    pipeline read from it (text): {"quality": readable | check | blurry, "sharpness", "contrast", "skew", "words", "confidence",
    "page", "said": the measures in words ("sharp, good contrast, 412 words read")}. The worst of its first three pages counts.
    None when the file can't be looked at (gone, not a picture): the caller keeps the old rule. A person's setting is never
    replaced by it (merge, set_quality)."""
    try:
        key_of = None
        if isinstance(source, (str, Path)):
            stat = Path(source).stat()
            key_of = (str(source), stat.st_mtime_ns, stat.st_size)
        measured = []
        cached = [(n, _MEASURED.get(key_of + (n,))) for n in list(pages or [1])[:3]] if key_of else []
        if cached and all(m is not None for _, m in cached):
            measured = cached
        else:
            for number, image in _page_pictures(source, pages):
                m = _picture(image)
                m["_image"] = image
                measured.append((number, m))
                if key_of:
                    _MEASURED[key_of + (number,)] = {k: v for k, v in m.items() if k != "_image"}
    except Exception:  # noqa: BLE001 -- a file that can't be opened or rendered: no estimate
        return None
    if not measured:
        return None
    order = {"readable": 0, "check": 1, "blurry": 2}
    words = _words_in(text) if text is not None else None
    graded = []
    for number, m in measured:
        said, grade = [], "readable"

        def worse(to: str) -> None:
            nonlocal grade
            grade = to if order[to] > order[grade] else grade

        if m["blank"]:
            said.append("nothing printed found on the page")
            worse("check")
        else:
            if m["sharpness"] >= SHARP:
                said.append("sharp")
            elif m["sharpness"] >= SOFT:
                said.append("a little soft")
                worse("check")
            else:
                said.append("blurry")
                worse("blurry")
            if m["contrast"] >= CONTRAST:
                said.append("good contrast")
            elif m["contrast"] >= FAINT:
                said.append("faint")
                worse("check")
            else:
                said.append("very faint")
                worse("blurry")
            if abs(m["skew"]) >= 2:
                said.append(f"tilted {MAX_SKEW} degrees or more" if abs(m["skew"]) >= MAX_SKEW else f"tilted {abs(m['skew']):g} degrees")
                if abs(m["skew"]) >= TILTED:
                    worse("check")
        graded.append((order[grade], number, grade, said, m))
    _, number, grade, said, m = max(graded, key=lambda g: (g[0], -g[1]))
    confidence, scan = None, None  # scan: True when the words were read from the picture, False when the page has a text layer of its own
    if expects_text and words is not None:
        scan = not (key_of and str(source).lower().endswith(".pdf") and len(_text_layer(Path(source), f"p{number}").strip()) >= 20)
        if ocr and scan and words < 30 and not m["blank"]:  # a page with its own text layer was never OCR'd: nothing to ask Tesseract
            if "ocr" in m:
                confidence = m["ocr"]
            elif m.get("_image") is not None:
                confidence = _ocr_confidence(m["_image"])
                if key_of:
                    _MEASURED[key_of + (number,)]["ocr"] = confidence
        if words == 0:
            said.append("no words read")
            grade = grade if order[grade] > order["check"] else "check"
        elif words < FEW_WORDS:
            said.append(f"only {words} word{'s' if words > 1 else ''} read")
            grade = grade if order[grade] > order["check"] else "check"
        else:
            said.append(f"{words} words read")
        if confidence is not None:  # in words: the number is Tesseract's own score, not a percentage of anything a person can check
            if confidence < UNSURE:
                said.append("most words read unsure")
                grade = "blurry"
            elif confidence < CONFIDENT:
                said.append("some words read unsure")
                grade = grade if order[grade] > order["check"] else "check"
            else:
                said.append("the words read clearly")
    page = f"page {number}: " if len(measured) > 1 and grade != "readable" else ""
    return {"quality": grade, "sharpness": m["sharpness"], "contrast": m["contrast"], "skew": m["skew"], "words": words,
            "confidence": confidence, "scan": scan, "page": number, "said": page + ", ".join(said)}


ENOUGH_WORDS = 20  # a page the reader classified and read this many words from was read in full, whatever its picture looks like


def graded(measured: dict[str, Any], doc_type: str) -> dict[str, Any]:
    """The estimate as the record keeps it. A measured "blurry" asks the client for another photo (portal/engine.py
    quality_retakes), and the thresholds are a first estimate: so a page the reader classified and read ENOUGH_WORDS words
    from is at worst "check" (a person looks; the client is not asked). "blurry" stays where nothing much was read."""
    if measured.get("quality") == "blurry" and doc_type != "unclassified" and (measured.get("words") or 0) >= ENOUGH_WORDS:
        return measured | {"quality": "check", "said": measured["said"] + " (read in full: a person checks it, the client is not asked)"}
    return measured


def source_of(client_id: str) -> str:
    """folder, or the connector a mirrored client's folder came from (connectors/sync.py local_id)."""
    m = re.search(r"-(fv|gd|ms|dw|cl)[0-9a-f]+$", client_id or "")
    return _CONNECTOR_PREFIX[m.group(1)] if m else "folder"


def _file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001 -- a file that can't be opened still gets its record
        return None


def _pages(part: str, total: int | None) -> list[int]:
    m = re.fullmatch(r"p(\d+)(?:-(\d+))?", part or "")
    if m:
        return list(range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1))
    return list(range(1, (total or 1) + 1))


# --- building -------------------------------------------------------------------


def build(source_dir: str | Path, classifications: dict[str, Any], extracted: dict[str, list], split_info: dict[str, dict] | None = None,
          *, texts: dict[str, str] | None = None, graph=None, source: str = "folder", uploads: Iterable[dict] | None = None,
          case_dir: str | Path | None = None, measure: bool = True, ocr: bool = True) -> dict[str, Any]:
    """The case's records from one processing run. classifications: doc id -> classify.Classification; extracted: doc id ->
    the extractors' fields; split_info: file name -> {"pages": n} (how many pages the file has: a part's pages come from its
    doc id); texts: doc id -> the text read; graph: the run's fact graph before derivation (whose document it is, by name;
    the client's country, for a document's language); uploads: the portal's upload records (when each arrived, a retake).
    measure: estimate each document's quality from its pages (measure_quality); ocr: let it ask Tesseract's confidence of a
    scanned page the pipeline read few words from."""
    folder = Path(source_dir)
    split_info, texts = split_info or {}, texts or {}
    by_file = {u.get("stored"): u for u in uploads or []}
    hashes: dict[str, str] = {}
    records: dict[str, dict[str, Any]] = {}
    children: dict[frozenset, str] = {}
    now = _now()
    for doc_id, classification in classifications.items():
        file, _, part = doc_id.partition("#")
        path = folder / file
        if file not in hashes:
            hashes[file] = _file_hash(path) or hashlib.sha256(texts.get(doc_id, doc_id).encode("utf-8")).hexdigest()
        digest = hashes[file]
        key = f"{digest}#{part}" if part else digest
        if key in records:  # the same file again: one record, two files
            if file not in records[key]["files"]:
                records[key]["files"].append(file)
                records[key]["doc_ids"].append(doc_id)
            continue
        doc_type = classification.doc_type
        info = type_info(doc_type)
        fields = extracted.get(doc_id) or []
        text = texts.get(doc_id, "")
        upload = by_file.get(file) or {}
        if upload.get("uploaded_at"):
            added = upload["uploaded_at"]
        else:
            try:
                added = datetime.fromtimestamp(path.stat().st_mtime, clock.zone()).isoformat()
            except OSError:
                added = now
        read_beyond = info.get("read_beyond_type", True)
        total = (split_info.get(file) or {}).get("pages") or (None if part else _page_count(path))
        pages = _pages(part, total)
        person, named = _person(doc_type, fields, text, _case_people(graph, doc_id), children)
        # the client's own country, for a certificate or passport that doesn't say its language: never for a relative's document
        case_country = (_case_value(graph, "applicant.citizenship" if doc_type in ("passport", "national_id") else "applicant.country_of_birth", doc_id)
                        if person in ("applicant", "unknown") else None)
        lang, lang_basis, lang_country = document_language(doc_type, text, fields, case_country)
        dated = read_dates(doc_type, fields, text) if read_beyond else read_dates(doc_type, [], "")
        retake = upload.get("status") == "retake"
        measured = None
        if measure and not retake and path.is_file():
            photo = doc_type in ("photograph", "passport_photo")
            measured = measure_quality(path, pages, text=text if read_beyond else None, expects_text=read_beyond and not photo, ocr=ocr)
        rule = quality(doc_type, text, retake)
        if measured:
            measured = graded(measured, doc_type)
            grade = measured["quality"]
            if rule == "blurry" and measured.get("scan") is not False:
                # an unclassified scan whose words read as nonsense stays blurry (an upside-down page), and the estimate says why. A page with
                # a text layer of its own (measured["scan"] is False) is not judged by its words: typed words in a language the reader does
                # not know (Haitian Creole) are not a sign of a bad picture, so its grade follows its measures
                grade = "blurry"
                if measured["quality"] != "blurry":
                    measured = measured | {"said": measured["said"] + ", but the words read are not ones the reader recognizes"}
            quality_basis = "measured"
        else:
            grade, quality_basis = rule, ("retake" if retake else "reader" if rule != "unknown" else "unknown")
        records[key] = {
            "id": (hashlib.sha256(key.encode()).hexdigest() if part else digest)[:16],
            "files": [file], "doc_ids": [doc_id], "pages": pages,
            "type": doc_type, "confidence": round(float(classification.confidence or 0.0), 2),
            "person": person, "person_set_by": None, "person_basis": "named" if named else "unknown",
            "language": lang, "language_basis": lang_basis, "language_country": lang_country,
            "issued": dated["issued"], "expires": dated["expires"], "issued_kind": dated["issued_kind"], "expires_kind": dated["expires_kind"],
            "dates_read": bool(fields) or bool((text or "").strip()), "identifiers": identifiers(fields),
            "quality": grade, "quality_set_by": None, "quality_basis": quality_basis,
            "quality_measures": {k: v for k, v in measured.items() if k != "quality"} if measured else None,
            # a portal upload the office itself added (a scan from the inbox, or a staff upload) says so in its own record
            "hash": digest, "source": upload["source"] if upload.get("source") in SOURCES else source if source in SOURCES else "folder", "added": added,
            "roles": list(info.get("roles") or []), "found": found_roles(doc_type, text) if read_beyond else {},
            "tags": [], "confidential": info.get("confidential"),
            # an engagement letter, attorney correspondence, the sealed envelope: never read beyond the type
            "text": text if read_beyond else "",
            "translated": None,  # the batch reads pages without translating them; a translation is recorded when one exists
        }
    data = {"version": VERSION, "built": now, "documents": list(records.values())}
    # a protected case protects every document in it (case_confidentiality); the type's flag otherwise.
    # The batch doesn't know the case folder yet: save_run applies it when the records are written there.
    return with_case_confidentiality(case_dir, data, graph) if case_dir is not None else data


def merge(previous: dict[str, Any] | None, built: dict[str, Any]) -> dict[str, Any]:
    """The new run's records with what reviewers set on the old ones: the person (with who and when), the language and the
    quality they set, their role tags, the translation made for it, and the first time each document was seen."""
    old = {r["id"]: r for r in (previous or {}).get("documents") or []}
    out = []
    for record in built.get("documents") or []:
        before = old.get(record["id"])
        if before:
            record = dict(record)
            if before.get("person_set_by"):
                record["person"], record["person_set_by"], record["person_basis"] = before["person"], before["person_set_by"], "set_by_person"
            if before.get("language_set_by"):
                record["language"], record["language_set_by"] = before["language"], before["language_set_by"]
                record["language_basis"], record["language_country"] = "set_by_person", None
                if before.get("translation_kept"):  # the answer to "Keep the translation?" stays with the language the person chose
                    record["translation_kept"] = before["translation_kept"]
            if before.get("quality_set_by"):
                record["quality"], record["quality_set_by"], record["quality_basis"] = before["quality"], before["quality_set_by"], "set_by_person"
            if before.get("dates_set_by"):  # the dates a reviewer read off the document (a green card's end date: no reader reads it yet)
                record["issued"], record["expires"], record["dates_set_by"] = before.get("issued"), before.get("expires"), before["dates_set_by"]
            record["tags"] = list(before.get("tags") or [])
            if before.get("added_by"):  # a document the office added in the review app: who and when (review/front_desk.py)
                record["added_by"] = before["added_by"]
            if before.get("translated"):  # the English text (src/translation.py), made once for this very file
                record["translated"] = before["translated"]
            record["added"] = min((x for x in (before.get("added"), record.get("added")) if x), key=clock.key) if before.get("added") else record.get("added")
        out.append(_with_roles(record))
    return {**built, "documents": out, "boundary_decisions": (previous or {}).get("boundary_decisions", {}),
            **{key: previous[key] for key in ("case_subjects", "subject_assignments") if previous and key in previous}}


def _with_roles(record: dict[str, Any]) -> dict[str, Any]:
    """The type's roles, then each role its own text shows (found_roles: a passport's entry stamp), then each role a reviewer
    tagged it with."""
    base = list(type_info(record.get("type", "unclassified")).get("roles") or [])
    base += [role for role in record.get("found") or {} if role not in base]
    return {**record, "roles": base + [t["role"] for t in record.get("tags") or [] if t.get("role") and t["role"] not in base]}


# --- whose it is, from the case as a whole (on every load: the case changes, a named document doesn't) ------------------
# A type that is about nobody in particular, or whose person the case can't assume: never "the client" by assumption.
_ABOUT_NOBODY = {"country_conditions", "news_article", "web_printout", "unclassified", "photograph", "declaration", "translation_certification",
                 "certified_translation", "attorney_correspondence", "engagement_letter"}
# A type that brings another person into the case: a citizen or resident relative's proof, a spouse, a family member's form.
_ANOTHER_PERSON = {"us_passport", "citizenship_certificate", "green_card", "us_birth_certificate", "marriage_certificate", "i130", "i130_approval",
                   "i730", "i730_approval", "i914a", "i918a", "i929", "i864"}
_EMPTY = {"", "NONE", "N/A", "NA", "NOT APPLICABLE", "NO", "0", "UNKNOWN"}
_GRAPHS: dict[tuple, Any] = {}


def _case_graph(client_dir: Path):
    """The case's fact graph (fact_graph.json, else the run's raw one), read once per change of the file; None without one."""
    from factgraph import FactGraph

    for name in ("fact_graph.json", "fact_graph_raw.json"):
        path = Path(client_dir) / name
        try:
            stat = path.stat()
        except OSError:
            continue
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in _GRAPHS:
            try:
                _GRAPHS.clear()  # one case at a time is enough: a page shows one case
                _GRAPHS[key] = FactGraph.load(path)
            except (OSError, ValueError, KeyError):
                return None
        return _GRAPHS[key]
    return None


def _filled(value: Any) -> bool:
    return str(value or "").strip().upper() not in _EMPTY


def _numbers(key: str, raw: Any, value: Any) -> tuple[str, str] | None:
    """(kind, the number written plainly) for a fact that is one of a person's numbers: SSN, A-Number, receipt, passport."""
    def code(v: Any) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())

    if key == "applicant.ssn":
        digits = re.sub(r"\D", "", str(value or ""))
        return ("ssn", digits) if len(digits) == 9 else None
    if key in ("applicant.a_number", "questionnaire.a_number"):
        digits = re.sub(r"\D", "", str(value or ""))
        return ("a_number", digits.zfill(9)) if 7 <= len(digits) <= 9 else None
    if key == "applicant.i360_receipt_number" or (key.startswith("applicant.") and key.endswith("_receipt_number")):
        return ("receipt", code(value)) if len(code(value)) == 13 else None
    if key.startswith("folder.uscis_case."):
        return ("receipt", code(raw)) if len(code(raw)) == 13 else None
    if key == "applicant.travel_document_number":
        return ("passport", code(value)) if len(code(value)) >= 5 else None
    if key.startswith("folder.passport."):
        number = code(key.split(".", 2)[2])
        return ("passport", number) if len(number) >= 5 else None
    return None


# Tracks and filings that bring another person into the case: a petitioner or relatives (family, I-730), an abuser (VAWA), the
# T or U visa's family members (src/journey.py track_of; packet.FILINGS ids as status.json and the packets name them).
_OTHER_PEOPLE_TRACKS = {"family", "vawa", "t_visa", "u_visa"}
_OTHER_PEOPLE_FILINGS = {"family", "vawa", "i914", "i914b", "u_visa", "u_cert", "i730"}


def one_person(records: list[dict[str, Any]], graph, status: dict[str, Any] | None = None, client_dir: str | Path | None = None) -> bool:
    """True when nothing in the case points to anyone but the client: no document someone placed (or the document itself
    placed) with a spouse, a child, a parent or a petitioner; no document that names someone the case doesn't know (a
    spouse's passport, another person's I-94: a second person, even unnamed by the case); no document of a kind that brings
    another person in (a relative's proof of status, a marriage certificate, a family member's form); not a VAWA, T or U case
    (8 U.S.C. 1367: case_confidentiality); not on a family, VAWA, T or U track (journey.track_of, or a person's choice), and no
    family, VAWA, T, U or I-730 filing mailed or built; and no petitioner, spouse, children, abuser, T or U family members or
    I-730 relatives in the facts."""
    for r in records:
        person = r.get("person") or "unknown"
        if person in ("spouse", "parent", "petitioner") or person.startswith("child_") or r.get("type") in _ANOTHER_PERSON:
            return False
        if person == "unknown" and r.get("person_basis") == "named":  # a name on it that is nobody on the case: someone else
            return False
    status = status or {}
    track = ((status.get("journey") or {}).get("track") or {}).get("value")
    filings = {f.get("filing") for f in status.get("filings") or []}
    if client_dir is not None:
        try:
            import journey

            filings.add(journey._latest_packet(Path(client_dir)).get("filing"))
            if not track and graph is not None:
                track = journey.track_of(graph, journey.notices(graph), journey._docs(Path(client_dir)), journey._latest_packet(Path(client_dir)).get("filing"))
        except Exception:  # noqa: BLE001 -- a case that can't be read is not assumed to be the client's alone
            return False
        try:
            if case_confidentiality(client_dir, graph) == "1367":
                return False
        except Exception:  # noqa: BLE001 -- the same: when in doubt, no assumption
            return False
    if track in _OTHER_PEOPLE_TRACKS or filings & _OTHER_PEOPLE_FILINGS:
        return False
    if graph is None:
        return True

    def value(key: str) -> Any:
        fact = graph.get(key)
        return fact.value if fact is not None else None

    for key in ("petitioner.status", "petitioner.family_name", "petitioner.given_name", "family.relationship", "applicant.spouse_given_name",
                "applicant.spouse_family_name", "vawa.classification"):
        if _filled(value(key)):
            return False
    if any(k.startswith(("vawa.abuser", "i730.r")) and _filled(f.value) for k, f in graph.all_facts().items()):
        return False  # the abuser's name or status, an I-730's relatives
    if str(value("applicant.marital_status") or "").strip().upper().startswith("MARRIED"):
        return False
    for key in ("applicant.total_children", "tvisa.family_count", "uvisa.members", "i730.count"):
        n = re.sub(r"\D", "", str(value(key) or ""))
        if n and int(n) > 0:
            return False
    return not any(re.fullmatch(r"applicant\.child\d+_(?:given|family)_name", k) and _filled(f.value) for k, f in graph.all_facts().items())


def infer_people(client_dir: str | Path, records: list[dict[str, Any]], graph=None) -> None:
    """Whose each document is, where neither the document nor a person said (person_basis): the client when it carries a number
    that is the client's -- the SSN, A-Number, receipt or passport number the client gave in the questionnaire or another
    document already the client's carries ("identifiers"); else the client when it names nobody and the case has one person only
    ("only_person", one_person). A name comes first: a document naming someone else that carries the client's number stays
    "unknown", with person_conflict "name_and_number" for the screen. Recomputed on every load, so a spouse's document added
    later takes the assumption back. The
    filings and the packet treat both as they treat a name on the document (docs/decisions.md 10/03/2026; the attorney confirms:
    docs/attorney_review.md)."""
    client_dir = Path(client_dir)
    graph = graph if graph is not None else _case_graph(client_dir)

    def basis(r: dict[str, Any]) -> str:
        if r.get("person_set_by"):
            return "set_by_person"
        if r.get("person_basis"):
            return r["person_basis"]
        return "named" if (r.get("person") or "unknown") != "unknown" else "unknown"  # a record built before person_basis existed

    for r in records:
        r["person_basis"] = basis(r)
        r.pop("person_conflict", None)
        if r.get("type") == "travel_history" and not r.get("person_set_by"):
            # The history-table classification establishes no holder. This
            # also retracts legacy automatic attribution; retain a named
            # reviewer's explicit ownership decision only.
            r["person"], r["person_basis"] = "unknown", "unknown"
        if r["person_basis"] in ("identifiers", "only_person"):  # an assumption: made again from the case as it is now
            r["person"], r["person_basis"] = "unknown", "unknown"
    mine = {d for r in records if r.get("person") == "applicant" and r["person_basis"] in ("named", "set_by_person") for d in r.get("doc_ids") or r.get("files") or []}
    numbers: dict[str, set[tuple[str, str]]] = {}
    known: set[tuple[str, str]] = set()
    for key, fact in (graph.all_facts().items() if graph is not None else []):
        for s in fact.sources:
            found = _numbers(key, s.raw_value, s.normalized_value)
            if found and found[1]:
                numbers.setdefault(s.doc_id, set()).add(found)
                if s.doc_type == "intake_questionnaire" or s.doc_id in mine:
                    known.add(found)
    status_path = client_dir / "status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    except (OSError, ValueError):
        status = {}
    for filing in status.get("filings") or []:  # a filing the firm mailed for this client, with its receipt
        receipt = re.sub(r"[^A-Z0-9]", "", str(filing.get("receipt") or "").upper())
        if len(receipt) == 13:
            known.add(("receipt", receipt))
    alone = one_person(records, graph, status, client_dir)
    for r in records:
        if r.get("type") == "travel_history":
            continue
        if r["person_basis"] == "set_by_person" or r.get("person") != "unknown":
            continue
        ids = r.get("doc_ids") or r.get("files") or []
        own = set().union(*[numbers.get(d, set()) for d in ids]) if ids else set()
        own |= {(kind, re.sub(r"[^A-Z0-9]", "", str((r.get("identifiers") or {}).get(kind) or "").upper())) for kind in ("receipt", "passport")}
        a_number = re.sub(r"\D", "", str((r.get("identifiers") or {}).get("a_number") or ""))
        if 7 <= len(a_number) <= 9:
            own.add(("a_number", a_number.zfill(9)))
        if any(n[1] for n in own & known):  # known holds only the client's own answers and documents already the client's: never this one
            if r["person_basis"] == "named":  # the name on it is someone else's: the name comes first; a person decides
                r["person_conflict"] = "name_and_number"
            else:
                r["person"], r["person_basis"] = "applicant", "identifiers"
        elif alone and r["person_basis"] != "named" and r.get("type") not in _ABOUT_NOBODY:
            r["person"], r["person_basis"] = "applicant", "only_person"


# --- reading and saving ------------------------------------------------------------


def read(client_dir: str | Path) -> dict[str, Any] | None:
    path = Path(client_dir) / FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save(client_dir: str | Path, data: dict[str, Any]) -> None:
    from subject_attribution import ensure_catalog
    ensure_catalog(data, Path(client_dir).name)
    path = Path(client_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    try:  # a paper the office marked absent that has arrived and been read: the mark is lifted (src/absence.py); never a reason to fail a save
        import absence

        absence.lift_arrived(client_dir, data.get("documents"))
    except Exception as exc:  # noqa: BLE001
        import sys

        sys.stderr.write(f"absence marks not checked ({type(exc).__name__})\n")


def save_run(client_dir: str | Path, built: dict[str, Any]) -> dict[str, Any]:
    """Writes a processing run's records, keeping what reviewers set before, each confidential as its case is, with whose each
    is as the whole case says (infer_people)."""
    data = with_case_confidentiality(client_dir, merge(read(client_dir), built))
    infer_people(client_dir, data["documents"])
    save(client_dir, data)
    events.record("documents", "built", f"Document records built from a reading run: {len(data['documents'])} documents", case_dir=client_dir, version=VERSION,
                  default_who=("The document reader", "system", "system"))
    return data


_TEXT_LAYERS: dict[tuple, str] = {}


def _text_layer(path: Path, part: str) -> str:
    """The text a PDF carries for these pages (a scan's OCR layer, a typed PDF's own text): what a case processed before
    documents.json existed has to show, read once per file and process. A photo has none: ""."""
    try:
        stat = path.stat()
    except OSError:
        return ""
    key = (str(path), stat.st_mtime_ns, stat.st_size, part)
    if key not in _TEXT_LAYERS:
        text = ""
        if path.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader

                reader = PdfReader(str(path))
                wanted = _pages(part, len(reader.pages))
                text = "\n".join(reader.pages[n - 1].extract_text() or "" for n in wanted if 1 <= n <= len(reader.pages))
            except Exception:  # noqa: BLE001 -- a file that can't be read shows what the fact graph holds
                text = ""
        _TEXT_LAYERS[key] = text
    return _TEXT_LAYERS[key]


def _fields_by_doc(graph) -> dict[str, list]:
    """doc id -> the fields its reader gave the fact graph (the sources that name it), in the readers' own shape."""
    from types import SimpleNamespace

    out: dict[str, list] = {}
    for key, fact in (graph.all_facts().items() if graph is not None else []):
        for s in fact.sources:
            out.setdefault(s.doc_id, []).append(SimpleNamespace(fact_key=key, raw_value=s.raw_value, normalized_value=s.normalized_value,
                                                                confidence=s.confidence))
    return out


def _refresh(record: dict[str, Any], folder: Path, graph, fields: dict[str, list], measure: bool) -> None:
    """A record written before brief G1 (no language_basis, dates_read or found): its language by type or country, its dates and
    found roles from the fields the fact graph holds and its text; and (measure) the quality of any record not measured yet,
    from its pages."""
    doc = (record.get("doc_ids") or record.get("files") or [""])[0]
    mine = fields.get(doc) or []
    text = record.get("text") or ""
    doc_type = record.get("type") or "unclassified"
    if "language_basis" not in record and not record.get("language_set_by"):
        country = _case_value(graph, "applicant.citizenship" if doc_type in ("passport", "national_id") else "applicant.country_of_birth", doc) \
            if record.get("person") in ("applicant", "unknown", None) else None
        if (record.get("language") or "unknown") == "unknown" or type_info(doc_type).get("language"):
            record["language"], record["language_basis"], record["language_country"] = document_language(doc_type, text, mine, country)
        else:
            record["language_basis"], record["language_country"] = "text", None
    if "dates_read" not in record:
        record["dates_read"] = bool(mine) or bool(text.strip())
        if not record.get("dates_set_by") and not (record.get("issued") or record.get("expires")):
            dated = read_dates(doc_type, mine, text)
            record.update({k: dated[k] for k in ("issued", "expires", "issued_kind", "expires_kind")})
    if "found" not in record:
        record["found"] = found_roles(doc_type, text)
    if measure and record.get("quality_basis") in (None, "reader", "unknown") and not record.get("quality_set_by"):
        file, _, part = doc.partition("#")
        path = folder / file
        info = type_info(doc_type)
        measured = measure_quality(path, record.get("pages") or _pages(part, None), text=text if info.get("read_beyond_type", True) else None,
                                   expects_text=info.get("read_beyond_type", True) and doc_type not in ("photograph", "passport_photo"),
                                   ocr=False) if path.is_file() else None
        if measured:
            measured = graded(measured, doc_type)
            # the rule's "blurry" (an unclassified page whose words read as nonsense) stands for a scan; a page with a text layer of its own follows its measures
            record["quality"] = "blurry" if record.get("quality") == "blurry" and measured.get("scan") is not False else measured["quality"]
            record["quality_basis"], record["quality_measures"] = "measured", {k: v for k, v in measured.items() if k != "quality"}


def load(client_dir: str | Path, measure: bool = False) -> dict[str, Any]:
    """The case's records -- with a record for every classified document the file doesn't hold yet (a case processed before
    documents.json existed, a document added by hand), built from what the case holds: its fact graph's sources for that
    document and the file's own text layer, so whose it is, its language and its dates show at once -- and whose each is as
    the whole case says (infer_people). measure: estimate the quality of records that have no estimate yet from their pages
    (the Documents page asks it; the packet and the filings, which only need whose and what, don't)."""
    client_dir = Path(client_dir)
    data = read(client_dir) or {"version": VERSION, "built": None, "documents": []}
    meta_path = client_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    known = {d for r in data["documents"] for d in r.get("doc_ids") or r.get("files") or []}
    missing = {doc: kind for doc, kind in (meta.get("classifications") or {}).items() if doc not in known}
    folder = Path(meta.get("source_folder") or client_dir)
    graph = _case_graph(client_dir)
    fields = _fields_by_doc(graph) if graph is not None and (missing or any("dates_read" not in r for r in data["documents"])) else {}
    if missing:
        from types import SimpleNamespace

        source = "portal" if folder.name == "uploads" else source_of(meta.get("client_id") or client_dir.name)  # portal/store.py keeps uploads/
        texts = {d: _text_layer(folder / d.partition("#")[0], d.partition("#")[2]) for d in missing}
        stub = build(folder, {d: SimpleNamespace(doc_type=k, confidence=0.0) for d, k in missing.items()}, {d: fields.get(d) or [] for d in missing},
                     texts=texts, graph=graph, source=source, measure=measure, ocr=False)
        ids = {r["id"] for r in data["documents"]}
        for r in stub["documents"]:
            if r["id"] in ids:  # the same file under another name: one record
                twin = next(x for x in data["documents"] if x["id"] == r["id"])
                twin.setdefault("doc_ids", list(twin["files"]))
                twin["files"] = twin["files"] + [f for f in r["files"] if f not in twin["files"]]
                twin["doc_ids"] = twin["doc_ids"] + [d for d in r["doc_ids"] if d not in twin["doc_ids"]]
            else:
                data["documents"].append(r)
    for r in data["documents"]:
        _refresh(r, folder, graph, fields, measure)
    data["documents"] = [_with_roles(r) for r in data["documents"]]
    infer_people(client_dir, data["documents"], graph)
    return data


def by_doc(client_dir: str | Path) -> dict[str, dict[str, Any]]:
    """doc id (as meta.json and the fact graph know it) -> its record."""
    return {d: r for r in load(client_dir)["documents"] for d in (r.get("doc_ids") or r.get("files") or [])}


def duplicates(client_dir: str | Path, exists=None) -> dict[str, str]:
    """doc id -> the doc id it duplicates (the same file uploaded again): only from documents.json, never guessed.
    exists(doc id), when given, says which copies are still there: the first copy still there is the one kept,
    so deleting it never leaves the document out altogether."""
    out = {}
    for r in (read(client_dir) or {}).get("documents") or []:
        ids = [d for d in r.get("doc_ids") or r.get("files") or [] if exists is None or exists(d)]
        for d in ids[1:]:
            out[d] = ids[0]
    return out


def _change(client_dir: str | Path, doc_id: str, who: str, change, action: str = "changed", what: str = "Changed a document", role: str | None = None,
            ledger: bool = True) -> dict[str, Any]:
    """One reviewer's change to one document record, kept and appended to the event ledger (src/events.py: what the change was, never its value)."""
    if not (who or "").strip():
        raise ValueError("Enter your name first: the change records who made it.")
    data = load(client_dir)
    record = next((r for r in data["documents"] if r["id"] == doc_id), None)
    if record is None:
        raise LookupError("unknown document")
    change(record)
    data["documents"] = [_with_roles(r) for r in data["documents"]]
    infer_people(client_dir, data["documents"])  # a document set as the spouse's ends "the only person on this case" for the rest
    save(client_dir, with_case_confidentiality(client_dir, data))
    done = next(r for r in data["documents"] if r["id"] == doc_id)
    if ledger:
        events.record("documents", action, f"{what} ({name(done.get('type') or 'unclassified')})", case_dir=client_dir, who=who, role=role, version=VERSION)
    return done


def set_person(client_dir: str | Path, doc_id: str, person: str, who: str, role: str | None = None) -> dict[str, Any]:
    """A reviewer says whose document it is; kept, with who and when, through every reprocessing."""
    if not valid_person(person):
        raise ValueError("Choose whose document it is: the client, the spouse, a child, the petitioner, a parent, or not known.")
    if not who.strip():
        raise ValueError("Enter your name first: the change records who made it.")

    def apply(r):
        r["person"], r["person_set_by"], r["person_basis"] = person, {"who": who.strip(), "role": role or "", "at": _now()}, "set_by_person"

    import jobs
    client_dir = Path(client_dir)
    with jobs.case_lock(jobs.folder_for(client_dir.parent), client_dir.name):
        before = next((r for r in (read(client_dir) or {}).get("documents", []) if r["id"] == doc_id), None)
        if before and before.get("person") != person:
            # Reopen before publishing the changed catalog owner. If the next
            # write fails, the conservative result is a review that remains open.
            __import__('subject_attribution').owner_changed(client_dir, doc_id, who, role)
        return _change(client_dir, doc_id, who, apply, "set_person", "Said whose the document is", role)


PERSON_WORDS = {"spouse": "the spouse", "petitioner": "the petitioner", "parent": "a parent"}


def person_words(person: str) -> str:
    """"the spouse", "a child" ... as a sentence says it."""
    return PERSON_WORDS.get(person) or ("a child" if person.startswith("child_") else person)


def set_aside_documents(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{doc id: its record} for every document a person said is someone else's (the spouse, a child, the petitioner, a parent): what it
    says is not the client's, so it must not fill the client's own boxes or drive the client's questions. Only a person's word counts
    (person_set_by): a document the system placed itself keeps working as before, and "Not known" settles nothing."""
    out = {}
    for r in records or []:
        person = r.get("person") or ""
        if r.get("person_set_by") and person not in ("applicant", "unknown", ""):
            for d in r.get("doc_ids") or r.get("files") or []:
                out[d] = r
    return out


def set_aside_for_other_people(graph, client_dir: str | Path) -> list[str]:
    """Filter this working copy by current named fact subjects. Catalog owner
    changes reopen review; they cannot restore accepted values on their own."""
    # Catalog ownership cannot remove every applicant-prefixed relative fact
    # from a multi-person document or accept an inferred applicant owner.
    from subject_attribution import filter_graph
    return sorted(filter_graph(graph, Path(client_dir)))


def person_changed(client_dir: str | Path, doc_id: str, who: str, role: str | None = None) -> str | None:
    """Log the catalog correction separately from current fact-subject review."""
    from factgraph import FactGraph
    from review import state

    client_dir = Path(client_dir)
    record = next((r for r in (read(client_dir) or {}).get("documents") or [] if r["id"] == doc_id), None)
    if record is None:
        return None
    ids = set(record.get("doc_ids") or record.get("files") or [])
    path = next((client_dir / n for n in ("fact_graph_raw.json", "fact_graph.json") if (client_dir / n).exists()), None)
    keys = sorted({k for k, f in (FactGraph.load(path).all_facts().items() if path else []) if k.startswith("applicant.")
                   and any(s.doc_id in ids for s in f.sources)})
    if keys and record.get("person_set_by") and record.get("person") not in ("applicant", "unknown", ""):
        try:  # the facts worked out from them move too (an I-94 set to a child changes Part 9): read the case with and without the document
            def view(graph):
                return {k: (f.status, str(f.value)) for k, f in graph.all_facts().items() if not k.startswith(("firm.", "companion."))}

            kept, dropped = view(state.reviewed_graph(client_dir, aside=False)), view(state.reviewed_graph(client_dir))
            keys = sorted(set(keys) | {k for k in kept.keys() | dropped.keys() if kept.get(k) != dropped.get(k)})
        except Exception:  # noqa: BLE001 -- the log still names what the document gave directly
            pass
    iid = f"document_person:{doc_id}"
    log = state.load_decisions(client_dir)
    who = (who or "").strip()
    if record.get("person") in ("applicant", "unknown", "") or not record.get("person_set_by"):
        if iid in log:
            state.undo_decision(client_dir, iid, who, role)
            return "Document owner updated. Review whose facts these are before using them in form values"
        return "Document owner updated. Review whose facts these are before using them in form values" if keys else None
    if not keys:
        return None
    named = person_words(record["person"])
    title = f"Document owner set to {named}: fact subjects need review"
    item = {"id": iid, "kind": "document_person", "level": "info", "title": title, "group": "other", "actions": ["acknowledge"],
            "facts": [{"key": k} for k in keys]}
    state.record_decision(client_dir, item, {"action": "acknowledge", "reviewer": who, "role": role,
                                             "note": f"{who} set this document to {named}. Corresponding fact-subject assignments were reopened. "
                                                     "Named subject review is required before these reads can supply form values or questions."})
    return "Document owner updated. Review whose facts these are before using them in form values"


def set_quality(client_dir: str | Path, doc_id: str, value: str, who: str, role: str | None = None) -> dict[str, Any]:
    if value not in QUALITIES:
        raise ValueError("Choose how readable it is: clear, hard to read, cut off, part missing, needs a check, or not checked.")

    def apply(r):
        r["quality"], r["quality_set_by"], r["quality_basis"] = value, {"who": who.strip(), "role": role or "", "at": _now()}, "set_by_person"

    return _change(client_dir, doc_id, who, apply, "set_quality", "Set how readable the scan is", role)


KEEP_TRANSLATION_QUESTION = "This document was marked as needing a translation. Keep that?"


def translation_question(client_dir: str | Path, doc_id: str, value: str) -> str | None:
    """The question to ask before a person changes a document from a language that needs a certified translation to one that does not
    (the birth certificate from Portuguese to English): one wrong click would drop the requirement. None when the change is no such change."""
    import translation

    record = next((r for r in load(client_dir)["documents"] if r["id"] == doc_id), None)
    if record and value in LANGUAGES and value != record.get("language") and translation.needs(record) and value not in translation.FOREIGN:
        return KEEP_TRANSLATION_QUESTION
    return None


def set_language(client_dir: str | Path, doc_id: str, value: str, who: str, role: str | None = None, keep_translation: bool | None = None) -> dict[str, Any]:
    """A reviewer says what language the document is printed in (an assumption by country corrected, a document the readers
    could not place); kept, with who and when, through every reprocessing. The translation step follows it (src/translation.py).
    A change that takes a document needing a translation to English must say whether the translation is kept (keep_translation): the
    answer is recorded on the document with who and when, and the document keeps its translation summary either way."""
    if value not in LANGUAGES:
        raise ValueError("Choose the language from the list.")
    if keep_translation is None and translation_question(client_dir, doc_id, value):
        raise ValueError(KEEP_TRANSLATION_QUESTION)

    def apply(r):
        import translation

        was = translation.foreign_language(r) if translation.needs(r) else None
        r["language"], r["language_set_by"] = value, {"who": who.strip(), "role": role or "", "at": _now()}
        r["language_basis"], r["language_country"] = "set_by_person", None
        if value in translation.FOREIGN:
            r.pop("translation_kept", None)  # a translation is needed again on its own
        elif was and keep_translation is not None:
            r["translation_kept"] = {"keep": bool(keep_translation), "language": was, "by": who.strip(), "role": role or "", "at": _now()}

    return _change(client_dir, doc_id, who, apply, "set_language", "Set the language the document is printed in", role)


def set_translated(client_dir: str | Path, doc_id: str, text: str | None, who: str) -> dict[str, Any]:
    """The document's English text (src/translation.py: the offline translator's, or the translator's own); None takes it back."""

    def apply(r):
        r["translated"] = (text or "").strip() or None

    return _change(client_dir, doc_id, who, apply, ledger=False)  # src/translation.py records the translation's own change


def set_dates(client_dir: str | Path, doc_id: str, value: str, who: str, role: str | None = None) -> dict[str, Any]:
    """A reviewer reads the document's dates off it: value is "<issued>|<expires>", each YYYY-MM-DD or empty. The expiry radar
    (src/expiry.py) watches a green card, driver's license, advance parole document or police clearance by these; kept, with who and
    when, through every reprocessing."""
    issued, _, expires = str(value or "").partition("|")
    dates = []
    for part in (issued.strip(), expires.strip()):
        if part:
            try:
                datetime.strptime(part, "%Y-%m-%d")
            except ValueError:
                raise ValueError("Enter each date as month, day and year.") from None
        dates.append(part or None)
    if dates[0] and dates[1] and dates[1] < dates[0]:
        raise ValueError("The end date is before the date it was issued: check both.")

    def apply(r):
        r["issued"], r["expires"], r["dates_set_by"] = dates[0], dates[1], {"who": who.strip(), "role": role or "", "at": _now()}

    return _change(client_dir, doc_id, who, apply, "set_dates", "Set the document's dates", role)


def tag(client_dir: str | Path, doc_id: str, role: str, who: str, role_of_who: str | None = None) -> dict[str, Any]:
    """A reviewer says what else the document proves ("this screenshot is a joint lease": good_faith). packet.plan counts it."""
    if role not in roles():
        raise ValueError("Choose what the document shows from the list.")

    def apply(r):
        if not any(t.get("role") == role for t in r.get("tags") or []):
            r["tags"] = list(r.get("tags") or []) + [{"role": role, "who": who.strip(), "role_of_who": role_of_who or "", "at": _now()}]

    return _change(client_dir, doc_id, who, apply, "tagged", f"Tagged the document as showing {role.replace('_', ' ')}", role_of_who)


def untag(client_dir: str | Path, doc_id: str, role: str, who: str, role_of_who: str | None = None) -> dict[str, Any]:
    if role not in roles():
        raise ValueError("Choose what the document shows from the list.")

    def apply(r):
        r["tags"] = [t for t in r.get("tags") or [] if t.get("role") != role]

    return _change(client_dir, doc_id, who, apply, "untagged", f"Took the tag {role.replace('_', ' ')} off the document", role_of_who)


# --- whose documents (src/family.py, vawa.py, i730.py, t_visa.py, u_visa.py) -----------------


def _classified(client_dir: Path) -> dict[str, str]:
    meta_path = Path(client_dir) / "meta.json"
    return (json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}).get("classifications") or {}


def documents_for(client_dir: str | Path, doc_types: Iterable[str] | None, people: Iterable[str], guess: bool = True) -> list[str]:
    """Doc ids of these types (None: any) that belong to one of these people ("child" stands for every child): the
    record's person first -- the document's own word, or a reviewer's. A document whose person isn't known (or that has
    no record) counts only when guess is True: the filing's assumption by type alone, as it was before the record."""
    client_dir = Path(client_dir)
    wanted, people = (set(doc_types) if doc_types is not None else None), set(people)
    records = by_doc(client_dir) if (client_dir / FILE).exists() else {}
    dupes = duplicates(client_dir)
    out = []
    for doc, kind in _classified(client_dir).items():
        if (wanted is not None and kind not in wanted) or doc in dupes:
            continue
        person = (records.get(doc) or {}).get("person") or "unknown"
        if person in people or (person.startswith("child_") and "child" in people) or (guess and person == "unknown"):
            out.append(doc)
    return out


def _plain(type_name: str) -> str:
    """'Passport' -> 'passport' inside a sentence; 'U.S. visa' and 'I-94 arrival record' stay as they are."""
    return type_name[0].lower() + type_name[1:] if len(type_name) > 1 and type_name[1].islower() else type_name


def person_note(client_dir: str | Path, title: str, groups: list[tuple[str, Iterable[str], Iterable[str] | None, bool]]) -> list[dict[str, str]]:
    """An info note for a filing's panel: per person (label, their person codes, the document types, whether a document
    nobody has placed counts for them by its type), the documents in the folder that are theirs, in the taxonomy's words."""
    classified = _classified(Path(client_dir))
    lines = []
    for label, people, doc_types, guess in groups:
        kinds: dict[str, int] = {}
        for d in documents_for(client_dir, doc_types, people, guess):
            kinds[classified[d]] = kinds.get(classified[d], 0) + 1
        if kinds:
            lines.append(f"{label}: " + ", ".join(_plain(name(k)) + (f" ({n})" if n > 1 else "") for k, n in kinds.items()))
    if not lines:
        return []
    return [{"level": "info", "title": title, "text": ". ".join(lines) + ". Whose each document is can be set on the Documents page."}]


def relatives_note(client_dir: str | Path, relationships: Iterable[str | None]) -> list[dict[str, str]]:
    """The documents of the family members a filing names (I-730 relatives, T and U Supplement A members), by the record's
    person only: nothing in a document's type says which relative it belongs to."""
    rels = [str(r or "") for r in relationships]
    groups = []
    if any(r.startswith("Spouse") for r in rels):
        groups.append(("The spouse's documents", ["spouse"], None, False))
    if any(r.lower().startswith(("child", "unmarried child")) and not r.lower().startswith("child of") for r in rels):
        groups.append(("The children's documents", ["child"], None, False))
    if any(r == "Parent" for r in rels):
        groups.append(("A parent's documents", ["parent"], None, False))
    return person_note(client_dir, "Family members' documents in the folder", groups)
