"""Every person on every case: who the case names (the client, the petitioner, the parents, the spouse, the children, the abuser on a VAWA
case, the other side in a court case, the sponsor, the relatives an I-730 brings), with every spelling of their name the case carries,
their dates of birth, A-Numbers, passport numbers and countries, their documents, and where each came from.

The rows live in the query layer's people table (src/query.py; data/query.db, owner-only, rebuilt per case when the case's records change and
by the nightly run). It is the one table in that file that holds a person's own data: the conflict search (src/conflicts.py) reads it. Nothing
here is a second copy kept anywhere else: rows are built again from the case's own records whenever they change.

What it is built from, per case:
  - the fact graph (fact_graph.json): every source of every fact that names a person (PERSON_KEYS below), each value as the document or the
    answer wrote it, with the document it came from;
  - the review decisions (decisions.json): a value a person typed or corrected on the review screen ("set");
  - the document records (documents.json, as the Documents tab reads them): whose each document is (applicant, spouse, petitioner, parent,
    child_n) and the A-Number and passport number read from it;
  - the people recorded on the case page (status.json, journey.people: G2's Person records) with their relationship and their documents' tag;
  - the conflict check made when the client was added (conflict_check.json): the client as the intake named them, and the other side the
    intake named (the adverse party, the abuser, the trafficker), with the role the person running the search gave.

A person named in two places as one (the VAWA abuser who is also the spouse; the parent named in the SIJ order who is the father) is one row:
the adverse role wins, the relationship stays.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import events
import name_match

FILE = "conflict_check.json"  # src/conflicts.py writes it; read here for the client and the other side the intake named

# The role a person has on a case, as a key, and in words (the conflict search shows these). ADVERSE: the other side.
ROLES = {"client": "The client", "petitioner": "The petitioner", "parent": "A parent", "spouse": "The spouse", "former_spouse": "A former spouse",
         "child": "A child", "relative": "A relative", "sponsor": "The sponsor", "beneficiary": "The person the client sponsors",
         "abuser": "The abuser named on a VAWA case", "trafficker": "The trafficker named on a T visa case", "adverse": "The other side in a court case",
         "other": "Someone else the case names"}
ADVERSE = ("abuser", "trafficker", "adverse")

# Who each kind of person is: (role, relationship to the client in words). A tag with a number ("child_2") uses its stem's entry.
PEOPLE = {"applicant": ("client", "The client"), "father": ("parent", "Father"), "mother": ("parent", "Mother"), "parent": ("parent", "Parent"),
          "spouse": ("spouse", "Spouse"), "former_spouse": ("former_spouse", "Former spouse"), "child": ("child", "Child"),
          "petitioner": ("petitioner", "The relative who petitions for the client"), "petitioner_parent": ("relative", "The petitioner's parent"),
          "abuser": ("abuser", "The person who abused the client (VAWA)"), "relative": ("relative", "A relative the client petitions for (I-730)"),
          "court_parent": ("adverse", "The parent named in the state court's SIJ order"), "parole_beneficiary": ("beneficiary", "The person the client sponsors for parole"),
          "sponsor": ("sponsor", "The client's sponsor"), "qualifying_relative": ("relative", "The qualifying relative on a waiver"),
          "principal": ("relative", "The principal applicant"), "party": ("other", "Named by the intake"), "person": ("other", "Recorded on the case page"),
          "parent_a": ("parent", "Parent"), "parent_b": ("parent", "Parent"), "uvisa_member": ("relative", "A family member on the U visa"),
          "tvisa_member": ("relative", "A family member on the T visa"), "petitioner_former_spouse": ("relative", "The petitioner's former spouse"),
          "n565_official": ("other", "The foreign official the special certificate is for")}

# The fact keys that name a person: (pattern, person tag (\1 is the pattern's number), field, name group). A name is put together from the given,
# middle and family words of one group read from one document; a full name stands alone. Fields: given, middle, family, full, dob, a_number,
# passport, country. Keys from src/assemble.py, src/family.py, src/vawa.py, src/i730.py, src/i360.py (sij), src/parole.py, the waivers, the
# cancellation and asylum modules and the questionnaire reader.
PERSON_KEYS: list[tuple[str, str, str, str]] = [
    # the client
    (r"applicant\.(given|middle|family)_name", "applicant", r"\1", "main"),
    (r"applicant\.(?:date_of_birth|dob)|questionnaire\.dob", "applicant", "dob", ""),
    (r"questionnaire\.other_dob\d+_date", "applicant", "dob", ""),
    (r"(?:applicant|questionnaire)\.(?:a_number|other_a_numbers)|feewaiver\.a_number", "applicant", "a_number", ""),
    (r"applicant\.travel_document_number|asylum\.passport_number", "applicant", "passport", ""),
    (r"applicant\.(?:country_of_birth|citizenship)", "applicant", "country", ""),
    (r"applicant\.(?:birth_name|current_legal_name|birth_certificate_name|marriage_certificate_name|name_current_typed|name_birth_typed)", "applicant", "full", ""),
    (r"applicant\.name_chosen_(given|family)", "applicant", r"\1", "chosen"),  # the name a reviewer typed on the names card (brief K6)
    (r"applicant\.name_change\.(?:new|former)_name", "applicant", "full", ""),  # a court's name change order (brief K1)
    (r"applicant\.i94_(given|family)_name", "applicant", r"\1", "i94"),
    (r"applicant\.other_name(\d+)_(given|family)", "applicant", r"\2", r"other\1"),
    (r"n400\.other_name(\d+)_(given|family)", "applicant", r"\2", r"n400other\1"),
    (r"g639\.other_(given|middle|family)_name", "applicant", r"\1", "g639other"),
    (r"i90\.card_(given|family)_name", "applicant", r"\1", "i90card"),
    (r"n565\.cert_(given|middle|family)_name", "applicant", r"\1", "certificate"),
    (r"g639\.entry_(given|middle|family)_name", "applicant", r"\1", "entry"),
    (r"(?:asylum\.other_names|questionnaire\.blank\.other_names|cancel\.(?:full_name|birth_name)|ijmotion\.respondent_name|bia\.client_name|feewaiver\.full_name)",
     "applicant", "full", ""),
    # the parents
    (r"applicant\.(father|mother)_(given|middle|family)_name", r"\1", r"\2", "main"),
    (r"applicant\.birth_cert\.parent_(a|b)_name", r"parent_\1", "full", ""),
    (r"applicant\.(father|mother)_birth_(given|family)_name", r"\1", r"\2", "birth"),
    (r"applicant\.birth_cert\.(father|mother)_name|questionnaire\.(father|mother)_(?:birth_)?name", r"\1\2", "full", ""),
    (r"applicant\.(father|mother)_dob", r"\1", "dob", ""),
    (r"applicant\.(father|mother)_country_of_birth", r"\1", "country", ""),
    # the spouse, a former spouse, the children
    (r"applicant\.spouse_(given|middle|family)_name", "spouse", r"\1", "main"),
    (r"applicant\.spouse_dob", "spouse", "dob", ""),
    (r"applicant\.spouse_a_number|i751\.spouse_a_number", "spouse", "a_number", ""),
    (r"applicant\.spouse_country_of_birth", "spouse", "country", ""),
    (r"cancel\.spouse_name|questionnaire\.spouse_name", "spouse", "full", ""),
    (r"applicant\.prior_spouse_(given|family)_name", "former_spouse", r"\1", "main"),
    (r"applicant\.prior_spouse_dob", "former_spouse", "dob", ""),
    (r"applicant\.child(\d+)_(given|middle|family)_name", r"child_\1", r"\2", "main"),
    (r"(?:applicant|questionnaire)\.child(\d+)_dob", r"child_\1", "dob", ""),
    (r"(?:applicant|questionnaire)\.child(\d+)_a_number", r"child_\1", "a_number", ""),
    (r"questionnaire\.child(\d+)_name", r"child_\1", "full", ""),
    # the petitioner (on a family case, also the sponsor on the I-864) and the petitioner's parents
    (r"petitioner\.(given|middle|family)_name", "petitioner", r"\1", "main"),
    (r"petitioner\.(given|family)_name_as_spouse", "petitioner", r"\1", "as_spouse"),
    (r"petitioner\.dob", "petitioner", "dob", ""),
    (r"petitioner\.a_number", "petitioner", "a_number", ""),
    (r"petitioner\.us_passport", "petitioner", "passport", ""),
    (r"petitioner\.country_of_birth", "petitioner", "country", ""),
    (r"petitioner\.parent(\d)_(given|family)_name", r"petitioner_parent_\1", r"\2", "main"),
    (r"petitioner\.parent(\d)_country_of_birth", r"petitioner_parent_\1", "country", ""),
    (r"petitioner\.prior_spouse(\d)_(given|family)_name", r"petitioner_former_spouse_\1", r"\2", "main"),
    # the abuser on a VAWA case
    (r"vawa\.abuser_(given|middle|family)_name", "abuser", r"\1", "main"),
    (r"vawa\.abuser_dob", "abuser", "dob", ""),
    (r"vawa\.abuser_a_number(?:_lpr|_naturalized|_other)?", "abuser", "a_number", ""),
    (r"vawa\.abuser_country_of_birth", "abuser", "country", ""),
    # the relatives an I-730 brings
    (r"i730\.r(\d+)_(given|middle|family)_name", r"relative_\1", r"\2", "main"),
    (r"i730\.r(\d+)_dob", r"relative_\1", "dob", ""),
    (r"i730\.r(\d+)_a_number", r"relative_\1", "a_number", ""),
    (r"i730\.r(\d+)_(?:country_of_birth|citizenship)", r"relative_\1", "country", ""),
    (r"i730\.r(\d+)_relationship", r"relative_\1", "relationship", ""),
    # the U visa's family members (Supplement A), one of whom may be the one who committed the crime: the other side
    (r"uvisa\.m(\d+)_(given|middle|family)_name", r"uvisa_member_\1", r"\2", "main"),
    (r"uvisa\.m(\d+)_dob", r"uvisa_member_\1", "dob", ""),
    (r"uvisa\.m(\d+)_a_number", r"uvisa_member_\1", "a_number", ""),
    (r"uvisa\.m(\d+)_(?:country_of_birth|citizenship)", r"uvisa_member_\1", "country", ""),
    (r"uvisa\.m(\d+)_relationship", r"uvisa_member_\1", "relationship", ""),
    (r"uvisa\.m(\d+)_perpetrator", r"uvisa_member_\1", "perpetrator", ""),
    # the T visa's family members (Supplement A)
    (r"tvisa\.m(\d+)\.(given|middle|family)_name", r"tvisa_member_\1", r"\2", "main"),
    (r"tvisa\.m(\d+)\.dob", r"tvisa_member_\1", "dob", ""),
    (r"tvisa\.m(\d+)\.a_number", r"tvisa_member_\1", "a_number", ""),
    (r"tvisa\.m(\d+)\.(?:country_of_birth|citizenship)", r"tvisa_member_\1", "country", ""),
    (r"tvisa\.m(\d+)\.relationship", r"tvisa_member_\1", "relationship", ""),
    # the SIJ order's parent: the other side in the state court
    (r"sij\.parent_name", "court_parent", "full", ""),
    # humanitarian parole: the person abroad and the sponsor
    (r"parole\.b(\d+)_(given|middle|family)_name", r"parole_beneficiary_\1", r"\2", "main"),
    (r"parole\.b(\d+)_dob", r"parole_beneficiary_\1", "dob", ""),
    (r"parole\.b(\d+)_a_number", r"parole_beneficiary_\1", "a_number", ""),
    (r"parole\.b(\d+)_(?:country_of_birth|citizenship)", r"parole_beneficiary_\1", "country", ""),
    (r"parole\.b(\d+)_relationship", r"parole_beneficiary_\1", "relationship", ""),
    (r"parole\.sponsor_(given|middle|family)_name", "sponsor", r"\1", "main"),
    (r"parole\.sponsor_dob", "sponsor", "dob", ""),
    (r"parole\.sponsor_a_number", "sponsor", "a_number", ""),
    # a waiver's qualifying relative, a cancellation's relatives, a Supplement A principal
    (r"(?:i601|i212|waiver)\.relative_(given|middle|family)_name", "qualifying_relative", r"\1", "main"),
    (r"(?:i601|i212|waiver)\.relative_dob", "qualifying_relative", "dob", ""),
    (r"(?:i601|i212|waiver)\.relative_a_number", "qualifying_relative", "a_number", ""),
    (r"cancel\.relative(\d)_name", r"relative_c\1", "full", ""),
    (r"cancel\.relative(\d)_dob", r"relative_c\1", "dob", ""),
    (r"(?:supa|applicant)\.principal_(given|family)_name", "principal", r"\1", "main"),
    (r"(?:supa|applicant)\.principal_a_number", "principal", "a_number", ""),
    # the foreign official the N-565's special certificate is for
    (r"n565\.official_(given|family)_name", "n565_official", r"\1", "main"),
]
_COMPILED = [(re.compile(p + r"$"), who, what, group) for p, who, what, group in PERSON_KEYS]
_PASSPORT_KEY = re.compile(r"folder\.passport\.([A-Z0-9]{6,})$")  # the reader files a passport's facts under its number (src/documents.py identifiers)
_NOT_A_NAME = {"NOT APPLICABLE", "N/A", "NA", "NONE", "UNKNOWN", "DESCONHECIDO", "NAO CONSTA", "NO", "YES"}

# The person tag the Documents tab gives (src/documents.py PEOPLE) -> this index's tag
DOC_TAGS = {"applicant": "applicant", "spouse": "spouse", "petitioner": "petitioner", "parent": "parent"}

# Where a value came from, when it is not a document of the taxonomy (the fact graph's own doc types)
_SOURCE_WORDS = {"intake_questionnaire": "The client's questionnaire", "paralegal_review": "Typed on the review screen", "office_question": "The office's question to the client",
                 "portal_answers": "The client's answers in the portal", "derived": "Worked out from the case's other facts"}


def kind_of(tag: str) -> tuple[str, str]:
    """(role, relationship in words) of a person tag: "child_2" -> ("child", "Child")."""
    stem = re.sub(r"_c?\d+$", "", tag)
    return PEOPLE.get(tag) or PEOPLE.get(stem) or ("other", "Someone the case names")


@lru_cache(maxsize=1024)  # the taxonomy's names change only with a release; asking it per value is a disk look per value
def _doc_words(doc_type: str | None) -> str:
    if not doc_type:
        return ""
    if doc_type in _SOURCE_WORDS:
        return _SOURCE_WORDS[doc_type]
    try:
        import index

        return index.type_name(doc_type)
    except Exception:  # noqa: BLE001
        return doc_type.replace("_", " ").capitalize()


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _as_written(raw: Any, normalized: Any) -> Any:
    """The value as the document or the answer wrote it (raw), when it is the same value as the reader's (accents, capitals and spacing aside);
    the reader's otherwise: a derived name's raw value is the reader's note on how it was split ("client wrote 'JOSE ...'. Split: ..."), not a name."""
    if raw in (None, ""):
        return normalized
    if normalized in (None, "") or not isinstance(normalized, str):
        return raw
    return raw if name_match.fold(raw) == name_match.fold(normalized) else normalized


def _named(value: Any) -> bool:
    text = name_match.fold(value)
    return bool(text) and text not in _NOT_A_NAME and any(len(w) > 1 for w in text.split())


class _Person:
    def __init__(self, tag: str):
        self.tag = tag
        self.role, self.relationship = kind_of(tag)
        self.parts: dict[tuple[str, str], dict[str, str]] = {}  # (group, document) -> {given, middle, family}
        self.part_from: dict[tuple[str, str], list[str]] = {}
        self.fulls: list[tuple[str, str, str]] = []  # (name, from, document)
        self.values: dict[str, list[tuple[str, str, str]]] = {"dob": [], "a_number": [], "passport": [], "country": []}  # (as written, from, document)
        self.documents: list[dict[str, str]] = []

    def empty(self) -> bool:
        return not (self.parts or self.fulls or any(self.values.values()))

    def names(self) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}

        def add(name: str, given: str | None, family: str | None, src: dict[str, str]) -> None:
            entry = out.setdefault(name, {"name": name, "given": given, "family": family, "sources": []})
            if src not in entry["sources"]:
                entry["sources"].append(src)
        fallback: dict[tuple[str, str], str] = {}  # a document that gave only the family name: the given name another source in the group gave
        for (group, _doc), parts in self.parts.items():
            for part in ("given", "family"):
                if parts.get(part):
                    fallback.setdefault((group, part), parts[part])
        for (group, doc), parts in self.parts.items():
            given = " ".join(x for x in (parts.get("given") or fallback.get((group, "given")), parts.get("middle")) if x)
            if not given:
                continue
            family = parts.get("family") or fallback.get((group, "family")) or ""
            add(f"{given} {family}".strip(), given, family or None, {"from": events.list_words(self.part_from[(group, doc)], 4), "document": doc})
        for name, where, doc in self.fulls:
            add(name, None, None, {"from": where, "document": doc})
        return list(out.values())

    def listed(self, field: str) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for written, where, doc, *read in self.values[field]:
            base = read[0] if read and read[0] not in (None, "") else written  # the reader's normalized value ("2006-03-14" for "14 MAR 2006"), else as written
            if field == "dob":
                d = name_match.parse_date(base) or name_match.parse_date(written)
                value = d.isoformat() if d else ""
            elif field == "a_number":
                value = name_match.a_number(base)
            elif field == "passport":
                value = name_match.passport(base)
            else:
                value = name_match.fold(written)
            if not value:
                continue
            entry = out.setdefault(value, {"value": value, "written": written, "sources": []})
            src = {"from": where, "document": doc}
            if src not in entry["sources"]:
                entry["sources"].append(src)
        return list(out.values())

    def row(self) -> dict[str, Any]:
        return {"person": self.tag, "role": self.role, "relationship": self.relationship, "names": self.names(), "birth_dates": self.listed("dob"),
                "a_numbers": self.listed("a_number"), "passports": self.listed("passport"), "countries": self.listed("country"), "documents": self.documents}


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _typed(log: dict[str, Any]) -> dict[str, Any]:
    """{fact key: value} a person set on the review screen, for every decision in force."""
    out: dict[str, Any] = {}
    for entry in log.values() if isinstance(log, dict) else ():
        if isinstance(entry, dict) and not entry.get("undone") and entry.get("action") == "set":
            for key, value in (entry.get("values") or {}).items():
                if value not in (None, ""):
                    out[key] = value
    return out


def rows(client_dir: str | Path, *, facts: dict[str, Any] | None = None, log: dict[str, Any] | None = None, status: dict[str, Any] | None = None,
         docs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The case's people, one dict per person (person, role, relationship, names, birth_dates, a_numbers, passports, countries, documents). The query
    layer passes what it has read already; anything not passed is read from the case folder."""
    client_dir = Path(client_dir)
    if facts is None:
        graph = _read(client_dir / "fact_graph.json")
        facts = graph.get("facts") if isinstance(graph, dict) and isinstance(graph.get("facts"), dict) else {}
    if log is None:
        log = _read(client_dir / "decisions.json")
        log = log if isinstance(log, dict) else {}
    if status is None:
        status = _read(client_dir / "status.json")
        status = status if isinstance(status, dict) else {}
    if docs is None:
        raw = _read(client_dir / "documents.json")
        docs = [r for r in (raw.get("documents") if isinstance(raw, dict) else None) or [] if isinstance(r, dict) and r.get("id")]
    by_doc = {}
    for r in docs:
        for name in r.get("doc_ids") or r.get("files") or []:
            by_doc[name] = r.get("type")
    people: dict[str, _Person] = {}

    def person(tag: str) -> _Person:
        return people.setdefault(tag, _Person(tag))

    def put(key: str, written: Any, doc_type: str | None, doc_id: str | None, read: Any = None) -> None:
        """One value of a fact (as written; read: the reader's normalized value) for the person the key names."""
        written = _text(written)
        if not written:
            return
        m = _PASSPORT_KEY.match(key)
        if m:
            person("applicant").values["passport"].append((m.group(1), "the passport's number", _doc_words(doc_type or by_doc.get(doc_id or ""))))
            return
        for pattern, who, what, group in _COMPILED:
            hit = pattern.match(key)
            if not hit:
                continue
            tag = hit.expand(who) if "\\" in who else who
            field = hit.expand(what) if "\\" in what else what
            grp = hit.expand(group) if "\\" in group else group
            doc = _doc_words(doc_type or by_doc.get(doc_id or ""))
            p = person(tag)
            where = events.words(key)
            if field in ("given", "middle", "family", "full", "country"):
                written = _text(_as_written(written, read))
            if field == "relationship":  # how a family member is related, as the answer gives it ("Spouse", "Unmarried child")
                if written and len(written) <= 60:
                    p.relationship = written
                return
            if field == "perpetrator":  # the U visa's family member who committed the crime: the other side
                if name_match.fold(written) in ("YES", "SIM", "SI"):
                    p.role = "adverse"
                return
            if field == "country" and len(written.split()) > 4:  # a reader's note, not a country
                return
            if field in ("given", "middle", "family"):
                if not _named(written):
                    return
                slot = p.parts.setdefault((grp, doc), {})
                slot.setdefault(field, written)
                p.part_from.setdefault((grp, doc), []).append(key)
            elif field == "full":
                for one in re.split(r"\s*;\s*", written):  # "other names" answers can list several
                    if _named(one):
                        p.fulls.append((one, where, doc))
            else:
                p.values[field].append((written, where, doc, None if read is None else _text(read)))
            return

    for key, f in facts.items():
        if not isinstance(f, dict):
            continue
        for s in f.get("sources") or []:
            if isinstance(s, dict):
                put(key, s.get("raw_value") if s.get("raw_value") not in (None, "") else s.get("normalized_value"), s.get("doc_type"), s.get("doc_id"),
                    s.get("normalized_value"))
        if f.get("status") == "resolved" and not f.get("sources") and f.get("value") not in (None, ""):
            put(key, f.get("value"), "derived", None)
    for key, value in _typed(log).items():
        put(key, value, "paralegal_review", None)

    for r in docs:  # whose each document is, and the numbers read from it
        tag = str(r.get("person") or "")
        tag = DOC_TAGS.get(tag) or (tag if re.fullmatch(r"child_\d+", tag) else "")
        if not tag:
            continue
        p = person(tag)
        p.documents.append({"id": str(r["id"]), "type": _doc_words(r.get("type"))})
        ids = r.get("identifiers") or {}
        if ids.get("a_number"):
            p.values["a_number"].append((str(ids["a_number"]), "the A-Number read from it", _doc_words(r.get("type"))))
        if ids.get("passport"):
            p.values["passport"].append((str(ids["passport"]), "the passport number read from it", _doc_words(r.get("type"))))

    for n, rec in enumerate(((status.get("journey") or {}).get("people") or []), start=1):  # G2's Person records on the case page
        if not isinstance(rec, dict):
            continue
        tag = str(rec.get("person") or "")
        tag = tag if tag in ("petitioner", "spouse") or re.fullmatch(r"child_\d+", tag) else f"person_{n}"
        p = person(tag)
        if tag.startswith("person_"):
            p.relationship = _text(rec.get("relationship")) or p.relationship
            p.role = "parent" if rec.get("person") == "parent" else {"Spouse": "spouse", "Child": "child", "Parent": "parent"}.get(_text(rec.get("relationship")), "relative")
        given, family = _text(rec.get("given_name")), _text(rec.get("family_name"))
        if given:
            p.parts[("case_page", "Recorded on the case page")] = {"given": given, "family": family}
            p.part_from[("case_page", "Recorded on the case page")] = ["the person's name on the case page"]

    check = _read(client_dir / FILE)
    if isinstance(check, dict) and not check.get("abandoned"):  # the client and the other side as the intake named them (never an add that stopped part way)
        subject = check.get("subject") or {}
        p = person("applicant")
        where = "Named when the client was added"
        for name in [subject.get("name"), *(subject.get("other_names") or [])]:
            if _named(name):
                p.fulls.append((_text(name), "the conflict search", where))
        for field in ("dob", "a_number", "passport"):
            if subject.get(field):
                p.values[field].append((str(subject[field]), "the conflict search", where))
        for n, party in enumerate(check.get("parties") or [], start=1):
            if not isinstance(party, dict) or not _named(party.get("name")):
                continue
            p = person(f"party_{n}")
            role = str(party.get("role") or "other")
            p.role = role if role in ROLES else "other"
            p.relationship = _text(party.get("relationship")) or ROLES[p.role]
            p.fulls.append((_text(party["name"]), "the conflict search", where))
            if party.get("dob"):
                p.values["dob"].append((str(party["dob"]), "the conflict search", where))

    out = [p for p in people.values() if not p.empty()]
    return [p.row() for p in _merge(out)]


def as_person(row: dict[str, Any]) -> name_match.Person:
    """A people row as the matcher reads it."""
    names = [n for n in (name_match.Name.parse(e.get("given"), e.get("family")) if e.get("given") else name_match.Name.parse(full=e.get("name"))
                         for e in row.get("names") or []) if n]
    births = [d for d in (name_match.parse_date(e.get("value")) for e in row.get("birth_dates") or []) if d]
    return name_match.Person(names, births, {e["value"] for e in row.get("a_numbers") or [] if e.get("value")},
                             {e["value"] for e in row.get("passports") or [] if e.get("value")})


def _merge(people: list[_Person]) -> list[_Person]:
    """One person named twice is one row: an adverse row (the abuser, the SIJ order's parent, the other side named at intake) whose name matches
    another person on the case (not the client) joins that person, who takes the adverse role; a parent from the case page joins the father or
    the mother whose name matches."""
    def same(a: _Person, b: _Person) -> bool:
        s = name_match.score(as_person(a.row()), as_person(b.row()))
        return bool(s and s.score >= 45 and not s.sentence.startswith("The given name and one surname"))  # the names match, or a number does
    kept: list[_Person] = []
    joining = lambda p: p.role in ADVERSE or p.tag.startswith("person_") or p.tag == "parent" or p.tag.startswith("parent_")  # noqa: E731
    for p in sorted(people, key=joining):  # the people they may join first
        joins = joining(p)
        target = next((q for q in kept if joins and q.tag != "applicant" and q.role not in ADVERSE and same(p, q)), None) if joins else None
        if target is None:
            kept.append(p)
            continue
        target.parts.update({k: v for k, v in p.parts.items() if k not in target.parts})
        target.part_from.update({k: v for k, v in p.part_from.items() if k not in target.part_from})
        target.fulls += p.fulls
        for field, values in p.values.items():
            target.values[field] += values
        target.documents += p.documents
        if p.role in ADVERSE:
            target.role = p.role
    return kept
