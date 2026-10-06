"""The client's names as a timeline, and the one name every form in the packet carries (wave K, brief K1).

Found on a real case (in shape only): the client married after her I-360 was approved. The marriage certificate carried her
name after marriage; the product filled Part 1 item 1 with her earlier name (the birth certificate's and the I-360 approval's)
and wrote NOT APPLICABLE in item 2, "Other names used", because the questionnaire's "other names" was blank. Both boxes were
wrong.

A name event is a name a document prints for the client, with its date and where it is:
  birth        the birth certificate's name, on the date of birth
  uscis        the name on a USCIS notice, on the notice's date: the I-360 approval's beneficiary in USCIS's own
               "FAMILY, GIVEN" split, and any other notice's Applicant or Beneficiary (src/extract/uscis_notice.py client_name)
               when it shares the client's given name (a family petition's other names are the relative's)
  marriage     the marriage certificate's name after marriage, on the marriage date: only from the certificate's own field
               ("Surname after Marriage" or "Name after Marriage", src/extract/marriage_certificate.py). A spouse's surname
               taken by custom is never evidence; a certificate that prints nothing there gives no event.
  court_order  a court's name change order: the new name, on the order's date (src/extract/name_change_order.py)
  client       the name the client typed (Tier 3, no date), and the other names the client typed ("client_other")

Only a birth certificate, the marriage certificate's own after-marriage field or a court order sets or changes the name
(decided by the main session on 10/03/2026, from the owner's review of a real packet, for the attorney to confirm): a USCIS
notice records the name USCIS was given, so a typo, a dropped surname or a receipt filed before the client's name changed never
becomes her legal name. The settled current legal name is the latest name-setting event, by date (birth, then the marriage
field, then a court order). An event whose date could not be read cannot prove it is the latest: it sits after the birth and
before every dated event (and the card says the date was not read). Every earlier distinct name from a name-setting document is
an "other name used", and so is every other name the client typed; a notice's spelling goes in item 2 only when the attorney
lists it on the card. Names are the same only when their words agree once accents, capitals, punctuation and the particles
(de, da, do, dos, das, e, del, y, the "la" of "de la") are set aside: no letter is forgiven (SOUZA and SOUSA differ). The
client's "no other names" never outranks a document that shows another name (the firm policy NA-OTHER-NAMES waits on
applicant.other_name1_family being absent). With no name-setting document in the folder nothing is settled.

One source of truth: settle() writes applicant.given_name / applicant.family_name (Part 1 item 1 of the I-485 and the name
boxes of every other form: G-28, G-1145, I-765, I-131, N-400 ...) and applicant.other_name{n}_given / _family (item 2 and the
other forms' "other names" boxes; a third and later name also goes to Part 14), each with the document it came from. A
reviewer's own decision on those boxes wins.

Two review cards (src/review/state.py): "Which name is current?" (kind "names", the fact applicant.name_current, a choice
among the names) whenever the name-setting documents and the client's typed name carry more than one name, or another document
(an I-94, a work permit) spells the settled name otherwise ("also seen on"); and, for the attorney, "USCIS knows the client by
another name" (kind "names_uscis", the facts applicant.name_uscis_ok and applicant.name_uscis_also) whenever a USCIS notice
spells the client's name differently from the settled name, by any letter. The product never files a name change request: the attorney decides whether an
explanation or evidence goes in the packet. Both are decisions like any other (decisions.json, the ledger, Undo), and an open
card holds the packet (src/packet.py counts open cards).

The timeline itself is stored in the fact graph under applicant.name_events (a JSON text: the events in order, the current
name, why, the other names), traced to the documents it was read from.

A marriage certificate with no after-marriage field (brief K6, from the shape of a real Massachusetts certificate: "Party A" and
"Party B" with Name, Residence, Age, Date of Birth ... and no "Surname after Marriage" row anywhere). The certificate gives no
name event, as above, and the product must not infer the married name; it asks. When the folder holds a marriage certificate that
names the client as a party (src/assemble.py applicant_party), no attributable post-marriage name was read, and no later source
sets a name, the question opens with read-based uncertainty and asks the person to check the client's printed field. Missing
extraction is never described as proof that the paper prints no field. Its
choices: the name as it stands (the latest name-setting document's, the birth certificate's), the client's typed answer to the
questionnaire's "current legal name" when there is one (applicant.name_current_typed: Tier 3, shown as "the client wrote:", never
settled by itself), and two EMPTY boxes a reviewer types the name into (applicant.name_chosen_given / _family; the spouse's surname
is shown beside them as information, never offered as a pick). Until a person chooses, item 1 keeps that name at Tier 3, nothing is
called an earlier name, the packet waits on the card, and applicant.name_after_marriage_open is present so the firm policy
NA-OTHER-NAMES never writes NOT APPLICABLE in item 2 meanwhile. A choice is a decision under the person's name (Undo reopens it): a
typed name becomes a "person" event, dated by the decision and signed with who made it, and is the settled name; the earlier name
goes to item 2; a USCIS notice that differs from it then opens the attorney's card as above.

Names Save retains an exact marriage-read snapshot beside the existing
document list. Changed or unavailable reads, including a changed value at
the same filename, reopen the names card while retaining the saved human
choice for review. Legacy choices with marriage evidence require a new
Save; this snapshot grants no subject/source/attorney approval.
"""

from __future__ import annotations

import json
import hashlib
import re
from typing import Any

from extract.names import fold_name, shares_given_name, split_name, surname_tokens
from factgraph import FactGraph
from factgraph.graph import REVIEW_DOC_ID

EVENTS_KEY = "applicant.name_events"
CURRENT_KEY = "applicant.name_current"
USCIS_KEY = "applicant.name_uscis_ok"
QUESTION_KEY = "applicant.name_after_marriage_open"  # present while the card asks which name is current after a marriage (brief K6)
CHOSEN_GIVEN, CHOSEN_FAMILY = "applicant.name_chosen_given", "applicant.name_chosen_family"  # the card's two empty boxes (brief K6)
CLIENT_CURRENT, CLIENT_BIRTH = "applicant.name_current_typed", "applicant.name_birth_typed"  # the questionnaire's answers (brief K6)
CLIENT_CHANGED = "questionnaire.name_changed"  # "Did your name change when you married or at any other time?"
UNSURE_CHANGED = "questionnaire.unsure.questionnaire.name_changed"  # the same question answered "I'm not sure" (src/portal/bank.py)
OVER_KEY = "applicant.name_choice_documents"  # the name-setting documents on the names card when a person chose (src/review/state.py)
EVIDENCE_KEY = "applicant.name_choice_evidence"  # exact marriage reads visible when that choice was saved
SAID_KEY = "applicant.name_change_said"  # present while the client says the name changed or is not sure: NA-OTHER-NAMES waits on it
NAME_DOC = "name timeline"  # the doc id of what the timeline itself works out: never a file
TYPED = ("intake_questionnaire", "office_question")  # the client's own typed answers
RANK = {"birth": 0, "uscis": 1, "marriage": 2, "marriage_name": 2, "court_order": 3, "person": 4, "client": 8, "client_current": 8,
        "client_birth": 8, "client_other": 9}
MAX_OTHER_NAMES = 2  # the I-485's Part 1 item 2 has two rows (Pt1Line2, Pt1Line2a); the rest go to Part 14
USCIS_WORDS = {"approval": "approval notice", "receipt": "receipt notice", "rfe": "request for evidence", "noid": "notice of intent to deny",
               "denial": "denial notice", "transfer": "transfer notice", "biometrics": "biometrics appointment notice",
               "interview": "interview notice", "rejection": "rejection notice"}


def _value(graph: FactGraph, key: str) -> Any:
    fact = graph.get(key)
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def _from_doc(graph: FactGraph, key: str, doc_id: str) -> Any:
    """What `key` says on one document (its own source), else nothing."""
    fact = graph.get(key)
    return next((s.normalized_value for s in (fact.sources if fact is not None else []) if s.doc_id == doc_id and s.normalized_value not in (None, "")), None)


# The particles the timeline sets aside (verification of K1, 10/03/2026): de, da, do, dos, das, e, del, y, and the "la" of "de la".
NAME_PARTICLES = frozenset({"DE", "DA", "DO", "DOS", "DAS", "E", "DEL", "Y"})


def name_key(name: str) -> tuple[str, ...]:
    """The words that make a name, with accents, capitals, punctuation and the particles set aside: "Ana Clara da Silva" and
    "ANA CLARA SILVA" are one name, "SOUZA-TESTE" and "SOUZA TESTE" too. Nothing else is forgiven."""
    words = re.sub(r"[-']", " ", fold_name(name or "")).split()
    out = []
    for i, w in enumerate(words):
        if w in NAME_PARTICLES or (w == "LA" and i > 0 and words[i - 1] == "DE"):
            continue
        out.append(w)
    return tuple(out)


def same_name(a: str, b: str) -> bool:
    """The timeline's own, strict comparison: equal once accents, capitals, punctuation and the particles are set aside. No letter is
    forgiven: SOUZA and SOUSA, MARIA and MARIO, LUIZA and LUISA are different names, and a difference opens a card. (The people index
    and the conflict search keep their own, looser matching: src/name_match.py, src/extract/names.py same_person_name.)"""
    ka = name_key(a)
    return bool(ka) and ka == name_key(b)


def page_of(doc_id: str) -> int:
    """0-based page in its file: a document split out of a combined PDF is "file.pdf#p3-4" (src/batch.py)."""
    m = re.search(r"#p(\d+)", doc_id or "")
    return int(m.group(1)) - 1 if m else 0


def _event(name: str, kind: str, date: str | None, doc: str, doc_type: str, tier: int, what: str, printed: str,
           given: str | None = None, family: str | None = None) -> dict[str, Any]:
    return {"name": fold_name(name), "given": fold_name(given) if given else None, "family": fold_name(family) if family else None,
            "kind": kind, "date": date if date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date)) else None, "doc": doc, "doc_type": doc_type,
            "page": page_of(doc), "tier": tier, "what": what, "printed": printed}


def _by_doc(graph: FactGraph, *keys: str, types: tuple[str, ...] | set[str] | None = None) -> dict[str, dict[str, Any]]:
    """{doc_id: {key: value, "_type": doc_type}} for the sources of `keys`, one entry per document."""
    out: dict[str, dict[str, Any]] = {}
    for key in keys:
        fact = graph.get(key)
        for s in fact.sources if fact is not None else []:
            if s.normalized_value in (None, "") or (types is not None and s.doc_type not in types):
                continue
            entry = out.setdefault(s.doc_id, {"_type": s.doc_type})
            entry.setdefault(key, str(s.normalized_value))
    return out


def _notices(graph: FactGraph) -> dict[str, dict[str, Any]]:
    """{doc_id: {"form", "kind", "date", "name"}} for every USCIS notice in the folder (src/extract/uscis_notice.py's facts)."""
    out: dict[str, dict[str, Any]] = {}
    facts = graph.all_facts()
    for key, fact in facts.items():
        if not key.startswith("folder.uscis_case."):
            continue
        parts = key.split(".")
        if len(parts) < 4:
            continue
        receipt, slug = parts[2], parts[3]
        for s in fact.sources:
            text = str(s.normalized_value or "")
            entry = out.setdefault(s.doc_id, {"type": s.doc_type})
            entry["form"] = text.split(" ", 1)[0] if re.match(r"[A-Z]{1,2}-\d", text) else None
            entry["kind"] = slug.split("_")[0]
            for name in ("date", "name"):
                found = facts.get(f"folder.notice.{receipt}.{slug}.{name}")
                value = next((x.normalized_value for x in (found.sources if found else []) if x.doc_id == s.doc_id), None)
                if value:
                    entry[name] = value
    return out


def _notice_words(notice: dict[str, Any] | None) -> str:
    notice = notice or {}
    what = USCIS_WORDS.get(notice.get("kind") or "", "notice")
    return f"USCIS {notice['form']} {what}" if notice.get("form") else f"USCIS {what}"


def events(graph: FactGraph) -> list[dict[str, Any]]:
    """Every name event in the case, in order: the documents' by date (an undated one after the birth, before every dated one),
    then the client's typed names."""
    out: list[dict[str, Any]] = []
    notices = _notices(graph)

    # The birth certificate's name, on the date of birth (the certificate's own date where it gave one).
    births = _by_doc(graph, "applicant.birth_certificate_name")
    for doc, entry in births.items():
        name = entry["applicant.birth_certificate_name"]
        out.append(_event(name, "birth", _from_doc(graph, "applicant.dob", doc) or _value(graph, "applicant.dob"), doc, entry["_type"], 1,
                          "Birth certificate", f"Name: {name}"))

    # The client's typed name: the given and family boxes of the questionnaire, the portal or the office's question.
    typed = _by_doc(graph, "applicant.given_name", "applicant.family_name", types=TYPED)
    typed_names = [" ".join(x for x in (e.get("applicant.given_name"), e.get("applicant.family_name")) if x) for e in typed.values()]

    # USCIS's own notices: the I-360 approval's beneficiary (USCIS's split), and every other notice's Applicant or Beneficiary.
    references = [e["name"] for e in out] + typed_names
    uscis = _by_doc(graph, "applicant.given_name", "applicant.family_name", types={"i360_approval"})
    for doc, entry in uscis.items():
        given, family = entry.get("applicant.given_name"), entry.get("applicant.family_name")
        if not (given and family):
            continue
        notice = notices.get(doc) or {"form": "I-360", "kind": "approval"}
        out.append(_event(f"{given} {family}", "uscis", notice.get("date"), doc, entry["_type"], 1, _notice_words(notice),
                          f"{family}, {given}", given, family))
        references.append(f"{given} {family}")
    for doc, notice in notices.items():
        if doc in uscis or not notice.get("name") or "," not in str(notice["name"]):
            continue
        family, _, given = str(notice["name"]).partition(",")
        full = f"{given.strip()} {family.strip()}"
        if references and not any(shares_given_name(full, r) for r in references):
            continue  # another person's notice (a relative's petition): not the client's name
        out.append(_event(full, "uscis", notice.get("date"), doc, notice.get("type") or "uscis_notice", 1, _notice_words(notice),
                          str(notice["name"]), given.strip(), family.strip()))

    # The marriage certificate's own field: the name after marriage, on the marriage date.
    out += _marriage_events(graph, out, typed)

    # A court's name change order: the new name, on the order's date.
    for doc, entry in _by_doc(graph, "applicant.name_change.new_name").items():
        name = entry["applicant.name_change.new_name"]
        out.append(_event(name, "court_order", _from_doc(graph, "applicant.name_change.date", doc), doc, entry["_type"], 1,
                          "Court order changing the name", f"New name: {name}"))

    # A name a person typed on the names card (brief K6): the decision's date and who made it.
    person = _person(graph)
    if person:
        out.append(person)

    documents = sorted(out, key=_order)
    client = []
    for doc, entry in typed.items():
        given, family = entry.get("applicant.given_name"), entry.get("applicant.family_name")
        if given or family:
            client.append(_event(" ".join(x for x in (given, family) if x), "client", None, doc, entry["_type"], 3,
                                 "The client's own answer: name", " / ".join(x for x in (given, family) if x), given, family))
    # The questionnaire's "current legal name" and "name at birth" (brief K6): the client wrote them; they never settle by themselves.
    for key, kind, what in ((CLIENT_CURRENT, "client_current", "The client's own answer: current legal name"),
                            (CLIENT_BIRTH, "client_birth", "The client's own answer: name at birth")):
        fact = graph.get(key)
        s = next((s for s in (fact.sources if fact is not None else []) if s.doc_type != REVIEW_DOC_ID and len(name_key(str(s.normalized_value or ""))) >= 2), None)
        if s is not None:
            client.append(_event(str(s.normalized_value), kind, None, s.doc_id, s.doc_type, 3, what, str(s.raw_value or s.normalized_value)))
    client += _typed_other_names(graph)
    return documents + client


def _person(graph: FactGraph) -> dict[str, Any] | None:
    """The name a reviewer typed in the names card's two boxes, as an event: dated by the decision, signed with who made it. A box left
    empty takes what Part 1, Item 1 holds now (a reviewer who types only the family name keeps the given name)."""
    import clock

    boxes = [graph.get(k) for k in (CHOSEN_GIVEN, CHOSEN_FAMILY)]
    typed = [str(f.review.chosen_value).strip() if f is not None and f.review is not None and f.review.chosen_value else "" for f in boxes]
    if not any(typed):
        return None
    given = typed[0] or str(_value(graph, "applicant.given_name") or "")
    family = typed[1] or str(_value(graph, "applicant.family_name") or "")
    if not (given and family):
        return None
    review = next(f.review for f, t in zip(boxes, typed) if t)
    day = clock.local_date(review.resolved_at)
    e = _event(f"{given} {family}", "person", day.isoformat() if day else None, REVIEW_DOC_ID, REVIEW_DOC_ID, 3,
               f"Chosen on the review screen by {review.resolved_by}", f"{fold_name(family)}, {fold_name(given)}", given, family)
    return e | {"who": review.resolved_by}


def _order(e: dict[str, Any]) -> tuple:
    if e["kind"] == "birth":
        return (0, e["date"] or "", 0)
    if e["date"]:
        return (2, e["date"], RANK[e["kind"]])
    return (1, "", RANK[e["kind"]])  # undated: it cannot prove it is the latest


def _marriage_read(graph: FactGraph, key: str, doc: str):
    """Conflicting reads of one party's field cannot silently pick the first."""
    fact = graph.get(key)
    sources = [s for s in fact.sources if s.doc_id == doc and s.doc_type == "marriage_certificate"] if fact else []
    values = {s.normalized_value for s in sources}
    return sources[0] if len(values) == 1 and next(iter(values)) not in (None, "") else None


def _marriage_events(graph: FactGraph, so_far: list[dict[str, Any]], typed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    from assemble import applicant_marriages

    out = []
    for cert, party, proven in applicant_marriages(graph):  # each certificate that names the client, read in the client's own column on it
        if not proven:
            continue  # only possibly the client's (no date of birth to show it): it may ask (_question), never set a name
        found = []
        for field, words in (("name_after", "Name after marriage"), ("surname_after", "Surname after marriage")):
            for doc, entry in _by_doc(graph, f"marriage.{party}.{field}").items():
                if doc != cert:
                    continue
                source = _marriage_read(graph, f"marriage.{party}.{field}", doc)
                state = _marriage_read(graph, f"marriage.{party}.after_read_state", doc)
                if source is None or graph.get(f"marriage.{party}.after_read_state") is not None and (state is None or state.normalized_value != "explicit"):
                    continue
                printed = str(source.normalized_value)
                date = _from_doc(graph, "applicant.marriage_date", doc) or _value(graph, "applicant.marriage_date")
                what = "Marriage certificate"  # the field it was read from is in "printed"
                if field == "name_after":
                    found.append(_event(printed, "marriage", date, doc, entry["_type"], 1, what, f"{words}: {printed}"))
                else:
                    # A surname comes only from this party's printed field.
                    name_key = f"marriage.{party}.name"
                    party_name = _marriage_read(graph, name_key, doc)
                    on_certificate = str(party_name.normalized_value) if party_name else ""
                    given, basis = _known_given(graph, so_far, typed, on_certificate, printed)
                    if given and fold_name(on_certificate).startswith(given + " "):
                        event = _event(f"{given} {printed}", "marriage", date, doc, entry["_type"],
                                       3 if basis.get("kind") == "unverified_split" or basis.get("kind") == "party_name_split" and not basis.get("proven") else 1,
                                       what, f"{words}: {printed}", given, printed)
                        found.append(event | {"given_parent_key": name_key, "given_parent_value": on_certificate,
                                              "composition": "given_name_and_printed_surname", "given_basis": basis})
                if found:
                    key = f"marriage.{party}.{field}"
                    found[-1].update(party=party, parent_key=key, parent_value=printed, page=source.page)
            if found:
                break  # a certificate that prints the whole name is read for it; the surname field is the same fact again
        out += found
    return out


def _known_given(graph: FactGraph, so_far: list[dict[str, Any]], typed: dict[str, dict[str, Any]], on_certificate: str, surname: str) -> tuple[str | None, dict]:
    for e in reversed([e for e in so_far if e["kind"] == "uscis" and e.get("given")]):
        if not on_certificate or fold_name(on_certificate).startswith(e["given"] + " "):
            # Keep the actual notice edge without a dependency on the output
            # applicant.given_name key, which the timeline itself can rewrite.
            candidates = [(key, source) for key, fact in graph.all_facts().items() for source in fact.sources
                          if source.doc_id == e["doc"] and source.doc_type == e["doc_type"]
                          and not source.from_facts and not source.input_evidence
                          and (key == "applicant.given_name" and fold_name(str(source.normalized_value)) == e["given"]
                               or key.startswith("folder.notice.") and key.endswith(".name")
                               and str(source.normalized_value) == e["printed"])]
            split_reads = [(key, source) for key, source in candidates if key == "applicant.given_name"]
            candidates = split_reads or candidates
            if len(candidates) == 1:
                key, source = candidates[0]
                return e["given"], {"kind": "notice_split", "key": key, "doc": source.doc_id, "doc_type": source.doc_type,
                                       "raw": source.raw_value, "value": source.normalized_value, "page": source.page,
                                       "instance": source.instance_id, "evidence": source.evidence_version,
                                       "reader": source.read_manifest, "issues": source.reading_issues}
            return e["given"], {"kind": "unverified_split", "doc": e["doc"], "given": e["given"]}
    if on_certificate:
        split = split_name(on_certificate, set(fold_name(surname).split()))
        if split.given and split.family:
            return split.given, {"kind": "party_name_split", "given": split.given, "proven": split.proven, "why": split.why}
    given = next((e.get("applicant.given_name") for e in typed.values() if e.get("applicant.given_name")), None)
    return (fold_name(given) if given else None), {"kind": "unverified_split"}


def _typed_other_names(graph: FactGraph) -> list[dict[str, Any]]:
    """The other names the client typed: the portal's repeat lines (questionnaire.other_name<n>_given / _family) and the
    scanned questionnaire's one line (applicant.other_names)."""
    out = []
    for n in range(1, 4):
        given, family = _value(graph, f"questionnaire.other_name{n}_given"), _value(graph, f"questionnaire.other_name{n}_family")
        if given or family:
            fact = graph.get(f"questionnaire.other_name{n}_given") or graph.get(f"questionnaire.other_name{n}_family")
            s = fact.sources[0]
            out.append(_event(" ".join(x for x in (given, family) if x), "client_other", None, s.doc_id, s.doc_type, 3,
                              "The client's own answer: other name used", " / ".join(x for x in (given, family) if x), given, family))
    fact = graph.get("applicant.other_names")
    for s in fact.sources if fact is not None else []:
        for one in re.split(r"\s*[;,]\s*", str(s.normalized_value or "")):
            if len(name_key(one)) >= 2:
                out.append(_event(one, "client_other", None, s.doc_id, s.doc_type, 3, "The client's own answer: other name used", one))
    return out


def distinct(names: list[str]) -> list[str]:
    out: list[str] = []
    for name in names:
        if name and not any(same_name(name, kept) for kept in out):
            out.append(name)
    return out


def _split(graph: FactGraph, e: dict[str, Any], evs: list[dict[str, Any]]):
    """(given, family, proven, why) of an event's name: its own split where the document prints one (USCIS's FAMILY, GIVEN; the
    certificate's surname after marriage), else the given name USCIS split for the client when this name starts with it, else
    the family convention (src/extract/names.py), proven only by a relative's surname."""
    from extract.names import NameSplit

    if e.get("given") and e.get("family"):
        proven = e["tier"] == 1
        why = ("the printed surname and the given name from this party's name, with the recorded notice split"
               if e.get("composition") and (e.get("given_basis") or {}).get("kind") == "notice_split"
               else "the printed surname and a split of this party's prior name" if e.get("composition")
               else f"{e['what']} prints the family name" if proven else "the client's own boxes")
        return NameSplit(e["given"], e["family"], proven, why)
    for other in reversed(evs):
        if other["kind"] == "uscis" and other.get("given") and e["name"].startswith(other["given"] + " "):
            return NameSplit(other["given"], e["name"][len(other["given"]) + 1:], True, "the given name as USCIS split it")
    from assemble import _family_hints

    hints = _family_hints(graph) | surname_tokens(*(f"X {o['family']}" for o in evs if o.get("family")))
    return split_name(e["name"], hints)


def _reviewed(graph: FactGraph, key: str) -> Any:
    fact = graph.get(key)
    return fact.review.chosen_value if fact is not None and fact.review is not None else None


def _us(iso: str | None) -> str:
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if iso and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso) else ""


def _label(e: dict[str, Any]) -> str:
    when = _us(e.get("date"))
    if e["kind"] == "person":  # a name a reviewer typed on the names card (brief K6)
        return f"the name chosen on the review screen by {e.get('who') or 'a reviewer'}" + (f" on {when}" if when else "")
    what = e["what"]
    what = what[:1].lower() + what[1:] if what[1:2].islower() else what  # "the birth certificate", "the USCIS I-360 approval notice"
    return f"the {what}" + (f" of {when}" if when else "")


SETTERS = ("birth", "marriage", "court_order")  # the only documents that set or change a name (decided 10/03/2026, see the module docstring)
NOTICES = ("uscis",)  # a government notice records the name the government was given: it never sets one
ALSO_KEY = "applicant.name_uscis_also"  # the attorney may list a notice's spelling as an other name used, on the card


def marriage_evidence(graph: FactGraph) -> str | None:
    """A change detector for actual applicant marriage reads, never approval.

    Missing legacy evidence identities stay missing; no proof is invented.
    """
    from assemble import applicant_marriages
    rows = []
    for doc, party, proven in applicant_marriages(graph):
        reads = []
        keys = [f"marriage.{party}.{field}" for field in ("name", "dob", "name_after", "surname_after", "name_unchanged", "after_read_state")]
        for key in keys + ["applicant.marriage_date", "applicant.marriage_place"]:
            fact = graph.get(key)
            for source in fact.sources if fact else []:
                if source.doc_id == doc and source.doc_type == "marriage_certificate":
                    reads.append({"key": key, "raw": source.raw_value, "value": source.normalized_value, "page": source.page,
                                  "instance": source.instance_id, "evidence": source.evidence_version,
                                  "reader": source.read_manifest, "issues": source.reading_issues})
        row = {"doc": doc, "party": party, "proven": proven, "reads": sorted(reads, key=lambda r: json.dumps(r, sort_keys=True))}
        compositions = [e for e in events(graph) if e.get("composition") and e["doc"] == doc and e.get("party") == party]
        if compositions:
            row["surname_composition"] = [{key: e.get(key) for key in ("name", "given_parent_key", "given_parent_value", "given_basis")}
                                          for e in compositions]
        rows.append(row)
    if not rows:
        return None
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda r: (r["doc"], r["party"])), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _also_seen(graph: FactGraph, evs: list[dict[str, Any]], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Other documents that print the client's name in the boxes Part 1 item 1 is filled from (an I-94, a work permit, a driver's
    license, a passport) but are not name events: where they print another spelling, the card says so ("also seen on ...")."""
    skip = {e["doc"] for e in evs} | {"paralegal_review", NAME_DOC}
    out = []
    for doc, entry in _by_doc(graph, "applicant.given_name", "applicant.family_name").items():
        if doc in skip or entry["_type"] in TYPED or entry["_type"] in ("paralegal_review", "derived", "firm_profile"):
            continue
        name = " ".join(x for x in (entry.get("applicant.given_name"), entry.get("applicant.family_name")) if x)
        if name and not same_name(name, current["name"]):
            out.append({"name": fold_name(name), "doc": doc, "doc_type": entry["_type"], "page": page_of(doc), "what": _doc_words(entry["_type"])})
    return out


def _doc_words(doc_type: str) -> str:
    try:
        import index

        return index.type_name(doc_type)
    except Exception:  # noqa: BLE001 -- the kind of document in words, or its own name
        return doc_type.replace("_", " ").capitalize()


def settle(graph: FactGraph) -> dict[str, Any] | None:
    """The timeline into the graph (applicant.name_events), the settled current name into Part 1 item 1 of every form, the
    other names into item 2. Called last in src/assemble.py's assemble(), so every reader's facts are in; returns the payload.

    The current name is the latest NAME-SETTING event: the birth certificate, then the marriage certificate's own after-marriage
    field, then a court order, by date. A notice (USCIS's I-797s) never sets it, never moves it and never lands in item 2 by
    itself: a notice that spells the name differently opens the attorney's card. With no name-setting document in the folder,
    nothing is settled: the name boxes are what the documents and the client's answers give, as before."""
    evs = events(graph)
    # The client says the name changed (or is not sure): NA-OTHER-NAMES never writes NOT APPLICABLE meanwhile (verification of K6)
    graph._facts.pop(SAID_KEY, None)
    said = "Yes" if _value(graph, CLIENT_CHANGED) == "Yes" else "Unsure" if graph.get(UNSURE_CHANGED) is not None else None
    if said:
        graph.add_source(SAID_KEY, NAME_DOC, "derived", f"the client answered {said} to whether the name changed", said, 1.0, tier=1)
    if not evs:
        return None
    setters = [e for e in evs if e["kind"] in SETTERS]  # in date order (events() sorts them)
    notices = [e for e in evs if e["kind"] in NOTICES]
    typed = [e for e in evs if e["kind"] in ("client", "client_current")]
    person = next((e for e in evs if e["kind"] == "person"), None)  # a name a reviewer typed on the card (brief K6)
    question = _question(graph, evs)  # a marriage certificate that prints no name after marriage for the client (brief K6)
    if question is None and said == "Yes" and not any(e["kind"] in ("marriage", "court_order", "client_current") for e in evs):
        question = _client_said_question(setters, typed, evs)  # the client says it changed, and nothing shows or gives the new name
    married_under = [_certificate_event(question)] if question and question.get("doc") and not setters else []  # the certificate's name
    pick = setters[-1] if setters else None
    chosen = _reviewed(graph, CURRENT_KEY)
    # a person may choose any name the case carries; with no name-setting document, a notice's too (then at Tier 3, below)
    current = next((e for e in [*reversed(setters), *typed, *married_under, *(notices if not setters else [])] if same_name(e["name"], str(chosen))), None) \
        if chosen else None
    if chosen and current is None:
        # A previously saved human choice survives loss of its former source
        # as a human choice requiring review, never a fabricated name setter.
        current = _event(str(chosen), "review_choice", _decided_on(graph), REVIEW_DOC_ID, "paralegal_review", 3,
                         "Previously reviewed name", "Name chosen on the review screen")
        current["page"] = None
    by = graph.get(CURRENT_KEY).review.resolved_by if current is not None else None
    if person:  # the boxes a reviewer typed win over a choice above them
        current, by = person, person["who"]
    # A name-setting document dated after the choice (a court order that came later) is never filed as an "other name" behind the
    # person's back: the card opens again, the chosen name is shown for now (Tier 3), and the newer document waits for a person.
    # The same for a name-setting document that was not on the card when the person chose, whatever its date or with none (re-verification
    # of K6): the decision records the name-setting documents it was made over (OVER_KEY); one that is not among them, and gives a name
    # neither chosen nor already on the card, reopens the card.
    newer: list[dict[str, Any]] = []
    unseen: list[dict[str, Any]] = []
    on = (current["date"] if current["kind"] == "person" else _decided_on(graph)) if by else None
    if by:
        newer = [e for e in setters if e["date"] and on and e["date"] > on and not same_name(e["name"], current["name"])]
        over = _decided_over(graph)
        if over is not None:
            seen = [e["name"] for e in setters if e["doc"] in over]
            unseen = [e for e in setters if e["doc"] not in over and e not in newer and not same_name(e["name"], current["name"])
                      and not any(same_name(e["name"], n) for n in seen)]
        else:  # a decision saved before the record was kept: an undated name change cannot be shown to be older than the choice
            unseen = [e for e in setters if e["kind"] != "birth" and not e["date"] and not same_name(e["name"], current["name"])]
    stale = {"by": by, "on": on, "setter": (newer or unseen)[-1], "unseen": not newer} if newer or unseen else None
    evidence = marriage_evidence(graph)
    recorded_evidence = _reviewed(graph, EVIDENCE_KEY)
    if by and (evidence is not None or recorded_evidence is not None) and recorded_evidence != evidence:
        affected = next((e for e in reversed(setters) if e["kind"] == "marriage"), None)
        if affected is None:
            from assemble import applicant_marriages
            certificates = applicant_marriages(graph)
            if certificates:
                doc, party, _ = certificates[-1]
                affected = _event(_from_doc(graph, f"marriage.{party}.name", doc) or current["name"], "marriage", None,
                                  doc, "marriage_certificate", 3, "Marriage certificate", "Check the current printed field")
                affected["page"] = question.get("page") if question else None
            else:
                affected = current | {"page": None}
        stale = {"by": by, "on": on, "setter": affected, "unseen": False, "evidence_changed": True,
                 "legacy_evidence": recorded_evidence is None, "source_unavailable": evidence is None}
        newer += [e for e in setters if e["doc"] == affected["doc"] and e not in newer]
    newer = newer + unseen  # nothing from either is settled or listed as an other name until a person chooses again
    if stale:
        by = None
    current = current or pick or (typed[0] if typed else evs[0])
    asking = question is not None and by is None  # the question is open until a person chooses
    settled = pick is not None or by is not None
    # An undated marriage or court order cannot be placed: beside another change it says nothing about which came last, and alone it is
    # still not proven to follow the others. Items 1 and 2 are then written at Tier 3 (a person confirms them); with another change
    # beside it, nothing is called earlier or current until a person chooses.
    changes = [e for e in setters if e["kind"] != "birth"]
    undated = [e for e in changes if not e["date"]]
    unordered = bool(undated) and len(changes) > 1 and not by
    weak = (bool(undated) and not by) or (by is not None and current["kind"] in NOTICES) or asking or bool(stale)
    also = _also_seen(graph, evs, current) if settled else []
    if settled:
        names = distinct([current["name"]] + [e["name"] for e in [*reversed(setters), *typed]])  # the current one first, then newest first
        differs = len(names) > 1 or bool(also)
    else:  # nothing sets the name: the card still opens when the documents and the client's answers disagree, and asks for the paper
        names = distinct([e["name"] for e in [*typed, *notices]])
        differs = len(names) > 1
    if asking:  # the choices: the name as it stands, the client's own answers, the certificate's name when nothing else sets one
        names = distinct([current["name"]] + names + [e["name"] for e in married_under])
        differs = True
    if stale and stale.get("evidence_changed"):
        why = (("The marriage evidence supporting the saved name choice is no longer attributable to the client. Review the source and choose again. "
                if stale.get("source_unavailable") else "The saved name choice has no matching snapshot of the current marriage reads. Check the client's printed field and choose again. ")
               + f"Until a person does, the forms show {current['name']} for a person to confirm.")
    elif stale and stale["unseen"]:
        why = (f"A document that was not on the card when the choice was made has arrived: {_label(stale['setter'])} sets a name "
               f"({stale['setter']['name']}). Choose again. Until a person does, the forms show {current['name']} for a person to confirm, "
               "and the new document's name is not listed as an other name.")
    elif stale:
        why = (f"{_label(stale['setter'])[:1].upper()}{_label(stale['setter'])[1:]} sets a name ({stale['setter']['name']}) and is dated after "
               f"the choice {stale['by']} made{' on ' + _us(stale['on']) if stale['on'] else ''}: choose again. Until a person does, the forms "
               f"show {current['name']} for a person to confirm, and the newer name is not listed as an other name.")
    elif by:
        why = f"Chosen by {by} on the review screen."
    elif asking:
        why = question["sentence"]
    elif unordered:
        why = (f"The order of the documents that change the name could not be read: {_label(undated[0])} has no date that could be "
               f"read. A person chooses the client's current name; until then the forms show {current['name']}, for a person to confirm.")
    elif pick is not None:
        why = (f"The latest document that sets a name is {_label(pick)}." + ("" if pick["date"] or pick["kind"] == "birth" else
               " Its date could not be read, so it cannot be shown to be the latest: check the order of the documents.")
               + (" A USCIS notice never sets the name: it records the name USCIS was given." if notices else ""))
    else:
        why = "No birth certificate, marriage certificate or court order in the folder sets the client's name."
    others = [e for e in reversed(setters) if not same_name(e["name"], current["name"]) and e not in newer] if settled else []
    others += [e for e in evs if e["kind"] == "client_other" and not same_name(e["name"], current["name"])]
    listed = _reviewed(graph, ALSO_KEY)  # a notice's spelling the attorney chose to list as an other name, on the card
    if listed:
        others += [e | {"tier": 3} for e in notices if same_name(e["name"], str(listed))][:1]
    other_names = distinct([e["name"] for e in others])
    others = [next(e for e in others if e["name"] == n) for n in other_names]
    # while the question is open, a notice is not yet compared with a filing name nobody has chosen (the attorney's card waits)
    uscis = [e for e in notices if not same_name(e["name"], current["name"])] if settled and not asking and not stale else []  # and while a stale choice waits
    said_no = _value(graph, "questionnaire.blank.other_names") == "Yes" or _value(graph, "questionnaire.used_other_names") == "No"
    if question:
        question = question | {"open": asking}
    payload = {"events": evs, "current": current, "why": why, "names": names, "differs": differs, "others": other_names,
               "uscis": uscis, "also_seen": also, "client_said_no": bool(said_no and other_names), "settled": settled,
               "overflow": max(0, len(other_names) - MAX_OTHER_NAMES), "unordered": unordered, "weak": weak,
               "decided": by is not None, "question": question, "client_wrote": _client_wrote(graph, evs), "marriage_evidence": evidence,
               "stale": {"by": stale["by"], "on": stale["on"], "name": stale["setter"]["name"], "unseen": stale["unseen"],
                         **({"evidence_changed": True, "legacy_evidence": stale["legacy_evidence"], "source_unavailable": stale["source_unavailable"]} if stale.get("evidence_changed") else {})} if stale else None}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    graph._facts.pop(EVENTS_KEY, None)  # worked out afresh on every reading: never two timelines side by side
    graph._facts.pop(QUESTION_KEY, None)
    fact = graph.get(CURRENT_KEY)
    if fact is not None and fact.review is None:
        graph._facts.pop(CURRENT_KEY, None)
    read_from = ", ".join(dict.fromkeys(e["doc"] for e in evs))
    graph.add_source(EVENTS_KEY, NAME_DOC, "derived", f"the client's names as read from {read_from}", text, 1.0, tier=1)
    if asking:  # the firm policy NA-OTHER-NAMES waits on this: no NOT APPLICABLE in item 2 while nobody has chosen
        graph.add_source(QUESTION_KEY, NAME_DOC, "derived", question["sentence"], "Yes", 1.0, tier=1)
    if differs and pick is not None and chosen is None and not asking and not stale:  # an open question has no pick: a person chooses
        graph.add_source(CURRENT_KEY, NAME_DOC, "derived", why, pick["name"], 0.9, tier=3 if weak else 1)
    if weak:  # an undated change, a notice's name a person chose, or an open question: written for a person to confirm
        current = current | {"tier": 3}
        others = [e | {"tier": 3} if e["kind"] in SETTERS or e["kind"] in NOTICES else e for e in others]
    if settled:
        _write_current(graph, current, _split(graph, current, evs), why)
        if current["kind"] == "person" and by:  # the reviewer typed the name: the decision is the sign-off of item 1, under their name
            for key in ("applicant.given_name", "applicant.family_name"):
                fact = graph.get(key)
                if fact is not None and fact.review is None and fact.status == "resolved" and fact.value:
                    graph.sign_off(key, by, "typed on the names card")
    elif asking:  # no birth certificate to keep: what the boxes hold waits for a person, never taken as the current name
        for key in ("applicant.given_name", "applicant.family_name"):
            fact = graph.get(key)
            if fact is not None and fact.review is None:
                fact.tier = 3
    for n, e in enumerate(others, start=1):
        _write_other(graph, n, e, _split(graph, e, evs))
    _part14(graph, others[MAX_OTHER_NAMES:])
    return payload


def _decided_over(graph: FactGraph) -> set[str] | None:
    """The name-setting documents (doc ids) that were on the names card when a person last chose there; None for a decision saved before
    this was recorded (then only a document dated after the choice reopens the card)."""
    fact = graph.get(OVER_KEY)
    if fact is None or fact.review is None or fact.review.chosen_value in (None, ""):
        return None
    try:
        return set(json.loads(str(fact.review.chosen_value)))
    except ValueError:
        return None


def _decided_on(graph: FactGraph) -> str | None:
    """The day a person chose on the names card (the decision's own date), as YYYY-MM-DD."""
    import clock

    fact = graph.get(CURRENT_KEY)
    day = clock.local_date(fact.review.resolved_at) if fact is not None and fact.review is not None else None
    return day.isoformat() if day else None


def _question(graph: FactGraph, evs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A marriage certificate in the folder that names the client as a party and prints no name after marriage for the client, with no court
    order dated on or after the marriage (brief K6): {doc, doc_type, page, date, name, spouse_family, spouse_name, sentence}; else None."""
    from assemble import applicant_marriages

    certificates = []  # each certificate that names the client (a parents' or a sibling's beside it is not one), with what it prints
    for doc, party, proven in applicant_marriages(graph):
        after = next((source.normalized_value for field in ("name_after", "surname_after")
                      if (source := _marriage_read(graph, f"marriage.{party}.{field}", doc)) is not None), None)
        if proven and any(e["kind"] == "marriage" and e["doc"] == doc for e in evs):
            continue
        if proven and _marriage_read(graph, f"marriage.{party}.name_unchanged", doc) is not None:
            continue  # An explicit unchanged marker answers only its own certificate.
        fact = graph.get(f"marriage.{party}.name")
        s = next((s for s in (fact.sources if fact is not None else []) if s.doc_id == doc and s.doc_type == "marriage_certificate"
                  and s.normalized_value), None)
        if s is not None:
            date = _from_doc(graph, "applicant.marriage_date", doc) or _value(graph, "applicant.marriage_date")
            certificates.append((date if date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date)) else None, s, party, proven, after))
    if not certificates:
        return None
    date, s, party, proven, after = sorted(certificates, key=lambda c: c[0] or "")[-1]  # the latest marriage
    if any(e["kind"] in {"court_order", "marriage"} and e["date"] and (not date or e["date"] > date or e["kind"] == "court_order" and e["date"] == date) for e in evs):
        return None  # a later document sets the name: the timeline has its answer
    other = "party_b" if party == "party_a" else "party_a"
    of = f" of {_us(date)}" if date else ""
    read_state = _from_doc(graph, f"marriage.{party}.after_read_state", s.doc_id) or "not_extracted"
    if any(graph.get(key) and len({source.normalized_value for source in graph.get(key).sources if source.doc_id == s.doc_id}) > 1
           for key in (f"marriage.{party}.name_after", f"marriage.{party}.surname_after", f"marriage.{party}.after_read_state")):
        read_state = "ambiguous"
    read_fact = graph.get(f"marriage.{party}.after_read_state")
    read_source = next((source for source in read_fact.sources if source.doc_id == s.doc_id), None) if read_fact else None
    if not proven:  # only possibly the client's: what it prints is shown, never read as the client's name (re-verification of K6)
        sentence = (f"The marriage certificate{of} names a party with the client's name but no date of birth that shows it is the client"
                    + (f"; it prints a name after marriage ({after})" if after else "")
                    + ". Check whether it is the client's certificate, then choose the name every form will carry.")
    elif after and read_state == "explicit":
        sentence = (f"The marriage certificate{of} has a post-marriage field read as {after}, but the read does not establish a full current name. "
                    "Check the client's printed field, then choose the name every form will carry.")
    elif read_state == "not_stated":
        sentence = (f"The client's post-marriage field on the marriage certificate{of} was read as blank or not stated. "
                    "Check the printed field, then choose the name every form will carry.")
    else:
        sentence = (f"No attributable post-marriage name was read for the client on the marriage certificate{of}. "
                    "Check the client's printed field, then choose the name every form will carry.")
    return {"doc": s.doc_id, "doc_type": s.doc_type, "page": read_source.page if read_source else None,
            "read_state": read_state, "party": party, "date": date, "name": fold_name(str(s.normalized_value)),
            "spouse_family": _from_doc(graph, "applicant.spouse_family_name", s.doc_id) or _value(graph, "applicant.spouse_family_name"),
            "spouse_name": _from_doc(graph, f"marriage.{other}.name", s.doc_id), "sentence": sentence, "proven": proven}


def _client_said_question(setters: list[dict[str, Any]], typed: list[dict[str, Any]], evs: list[dict[str, Any]]) -> dict[str, Any]:
    """The client answered that the name changed, but no document shows a new name and the client typed none (verification of K6): the
    names card asks for it, with the two empty boxes, and no NOT APPLICABLE is written meanwhile."""
    shown = (setters[-1] if setters else typed[0] if typed else evs[0])["name"]
    return {"kind": "client_said", "doc": None, "doc_type": None, "page": 0, "date": None, "name": shown, "spouse_family": None,
            "spouse_name": None,
            "sentence": ("The client answered that the name changed, but no new name was established from the document readings and the client did not "
                         "write one. Ask the client for the current legal name and the paper that changed it (a marriage certificate or a "
                         "court order), then choose the name every form will carry.")}


def _certificate_event(question: dict[str, Any]) -> dict[str, Any]:
    """The name the certificate prints for the client, as a choice when no birth certificate is in the folder: a record of the name the
    client married under, never a name-setting event by itself."""
    event = _event(question["name"], "marriage_name", question["date"], question["doc"], question["doc_type"], 1,
                   "Marriage certificate (the name the client married under)", f"Name: {question['name']}")
    event["page"] = question.get("page")
    return event


def _client_wrote(graph: FactGraph, evs: list[dict[str, Any]]) -> dict[str, Any]:
    """What the client answered to the questionnaire's name questions (brief K6), for the card's "the client wrote:" lines."""
    said = {e["kind"]: e["name"] for e in evs if e["kind"] in ("client_current", "client_birth")}
    return {"current": said.get("client_current"), "birth": said.get("client_birth"), "changed": _value(graph, CLIENT_CHANGED)}


def _part14(graph: FactGraph, rest: list[dict[str, Any]]) -> None:
    """A third and later other name: the I-485 has two rows in Part 1 item 2, so the rest go to Part 14 (additional information),
    one block pointing at page 1, Part 1, item 2, through the same path the address and job history use (src/assemble.py)."""
    if not rest:
        return
    from assemble import _p14_spot, _p14_text

    n = 1
    while graph.get(f"applicant.p14_block{n}_text") is not None:
        n += 1
    text = _p14_text("OTHER NAMES USED (CONTINUED)", [e["name"] for e in rest])
    page, part, item = _p14_spot("applicant.na.other_names")  # where the form prints the other names: read from the edition, never typed
    for key, value in (("page", page), ("part", part), ("item", item), ("text", text)):
        if not value:  # a spot the form cannot vouch for stays blank, and is flagged for a person (batch.part14_spot_flags)
            continue
        graph.add_source(f"applicant.p14_block{n}_{key}", rest[0]["doc"], rest[0]["doc_type"],
                         f"composed from the client's name timeline ({len(rest)} more other name{'s' if len(rest) != 1 else ''})", value, 0.9,
                         tier=3 if any(e["tier"] == 3 for e in rest) else 1)


def _put(graph: FactGraph, key: str, value: str, e: dict[str, Any], raw: str) -> bool:
    """One source for `key` from the event's document; a disagreement it makes is settled for this value. False when a
    reviewer's own decision holds the box."""
    fact = graph.get(key)
    if fact is not None and fact.review is not None:
        return False
    if fact is None or not any(s.normalized_value == value for s in fact.sources):
        # A name split is a derivation of its actual name-setting read,
        # not a new unbound original edge merely citing the same PDF. Missing
        # or mismatched parents retain the legacy/unbound source guard.
        parent_key = {"birth": "applicant.birth_certificate_name",
                      "court_order": "applicant.name_change.new_name",
                      "marriage": e.get("parent_key") if e.get("parent_key", "").endswith(".name_after") else None}.get(e.get("kind"))
        parent = graph.get(parent_key) if parent_key else None
        inputs = [parent_key] if parent is not None and key != parent_key and any(
            source.doc_id == e["doc"] and source.doc_type == e["doc_type"]
            and same_name(str(source.normalized_value or ""), e["name"]) for source in parent.sources) else None
        if e.get("composition") == "given_name_and_printed_surname":
            parents = [(e.get("parent_key"), e.get("parent_value")), (e.get("given_parent_key"), e.get("given_parent_value"))]
            if all(parent_key and parent_key != key and graph.get(parent_key) and any(
                    s.doc_id == e["doc"] and s.doc_type == e["doc_type"] and s.normalized_value == expected
                    for s in graph.get(parent_key).sources) for parent_key, expected in parents):
                inputs = [parent_key for parent_key, _ in parents]
        basis = e.get("given_basis") or {}
        graph.add_source(key, e["doc"], e["doc_type"], raw, value, 0.9, tier=e["tier"], from_facts=inputs, page=e.get("page"),
                         input_evidence=[basis["evidence"]] if inputs and basis.get("evidence") else None)
    fact = graph.get(key)
    if fact.status == "conflict" or fact.value != value:
        fact.status = "conflict"  # an earlier settlement chose another value: settled again, for this one
        graph.resolve_conflict(key, value, raw, "the name timeline")
    return True


def _write_current(graph: FactGraph, e: dict[str, Any], split, why: str) -> None:
    """Part 1 item 1 from the settled name: left as it is when the boxes already hold that name (whatever split they carry), so a
    case with one name everywhere is untouched."""
    given, family = _value(graph, "applicant.given_name"), _value(graph, "applicant.family_name")
    if given and family and same_name(f"{given} {family}", e["name"]):
        if e["tier"] == 3:  # the right name already, but nothing proves it is the current one: a person confirms it
            for key in ("applicant.given_name", "applicant.family_name"):
                fact = graph.get(key)
                if fact.review is None:
                    fact.tier = 3
        return
    raw = f"{e['printed']} ({e['what']}{', ' + _us(e['date']) if e.get('date') else ''}). {why} Split: {split.why}"
    wrote = [_put(graph, "applicant.given_name", split.given, e, raw), _put(graph, "applicant.family_name", split.family, e, raw)]
    if any(wrote) and (not split.proven or e["tier"] == 3):
        for key in ("applicant.given_name", "applicant.family_name"):
            fact = graph.get(key)
            if fact is not None and fact.review is None:
                fact.tier = 3  # nothing proves where the given name ends: a person confirms it


def _write_other(graph: FactGraph, n: int, e: dict[str, Any], split) -> None:
    raw = f"{e['printed']} ({e['what']}{', ' + _us(e['date']) if e.get('date') else ''}): an earlier name. Split: {split.why}"
    for part, value in (("given", split.given), ("family", split.family)):
        if value:
            _put(graph, f"applicant.other_name{n}_{part}", value, e, raw)
            fact = graph.get(f"applicant.other_name{n}_{part}")
            if (not split.proven or e["tier"] == 3) and fact.review is None:
                fact.tier = 3


# --- what the review screen and the case page read ------------------------------------------------------------------


def payload(graph: FactGraph) -> dict[str, Any] | None:
    value = _value(graph, EVENTS_KEY)
    try:
        return json.loads(value) if value else None
    except ValueError:
        return None


def flags(graph: FactGraph) -> list:
    """The two cards, as flags (batch.cross_check): open until a decision settles them for the names the case has now; and a line in
    the flag report when other names go on to Part 14."""
    from validate import Flag

    data = payload(graph)
    if not data:
        return []
    out = []
    current = data["current"]
    if data["differs"] or data.get("stale"):
        chosen = _reviewed(graph, CURRENT_KEY)
        question = data.get("question") or {}
        if "decided" in data:  # a person's choice the timeline could place (a name typed on the card, or one of the names the case carries)
            still_open = not data["decided"]
        else:  # a timeline saved before brief K6
            still_open = chosen is None or not any(same_name(str(chosen), n) for n in data["names"])
        if still_open:
            others = ", ".join(data["others"]) or "none"
            if question.get("open"):
                words = (f"{question['sentence']} Until a person chooses, Part 1, Item 1 of every form shows {current['name']}, for a person to "
                         "confirm, and Part 1, Item 2 gets no NOT APPLICABLE. The product does not infer a married name: choose the name as it "
                         "stands, the client's own answer, or type the name the client uses now.")
                wrote = data.get("client_wrote") or {}
                if wrote.get("current"):
                    words += f" The client wrote: {wrote['current']} (current legal name)."
                if wrote.get("birth"):
                    words += f" The client wrote: {wrote['birth']} (name at birth)."
                if wrote.get("changed") in ("Yes", "No"):
                    words += " The client answered that the name changed." if wrote["changed"] == "Yes" else " The client answered that the name did not change."
            elif not data.get("settled", True):
                said = "; ".join(f"{e['name']} ({_label(e)})" for e in data["events"] if e["kind"] in ("client", *NOTICES))
                words = ("No birth certificate, marriage certificate or court order in the folder sets the client's name, and the "
                         f"documents and the client's answers disagree: {said}. Ask the client for the birth certificate (and the marriage "
                         "certificate or court order if the name changed). Until then Part 1, Item 1 shows what the documents give.")
            elif data.get("unordered"):
                words = (f"{data['why']} Until a person chooses, Part 1, Item 1 of every form shows {current['name']} and Part 1, Item 2 "
                         f"the other names: {others}. Neither is taken as the order of events.")
            else:
                words = (f"The name-setting documents and the client's answers carry {len(data['names'])} "
                         f"name{'s' if len(data['names']) != 1 else ''} for the client. {data['why']} "
                         f"Part 1, Item 1 of the I-485 and the name boxes of every other form in the packet use {current['name']}; "
                         f"Part 1, Item 2 (other names used) gets the earlier names: {others}.")
            for seen in data.get("also_seen") or []:
                what = seen["what"][:1].lower() + seen["what"][1:] if seen["what"][1:2].islower() else seen["what"]  # "the I-94 arrival record"
                words += f" Also seen on the {what}: {seen['name']}."
            if data.get("client_said_no"):
                words += " The client answered that they never used another name, but a document shows one: the document is used."
            out.append(Flag("review", CURRENT_KEY, words, kind="names"))
    if data["uscis"]:
        fact = graph.get(USCIS_KEY)
        if fact is None or fact.review is None or name_key(str(fact.review.chosen_value)) != name_key(current["name"]):
            known = "; ".join(f"{e['name']} ({_label(e)})" for e in data["uscis"])
            out.append(Flag("review", USCIS_KEY,
                            f"USCIS knows the client as {known}; this filing will say {current['name']} ({_label(current)}). A notice "
                            "records the name USCIS was given; it never sets the client's name. The attorney decides whether an "
                            "explanation or evidence of the name change goes in the packet, and whether a notice's spelling is listed in "
                            "Part 1, Item 2. Nothing is filed as a name change request.", kind="names_uscis"))
    if data.get("overflow"):
        n = data["overflow"]
        out.append(Flag("informational", "applicant.other_name1_family",
                        f"OTHER NAMES: {len(data['others'])} other names used; Part 1, Item 2 of the I-485 has two rows, so {n} more "
                        f"{'goes' if n == 1 else 'go'} in Part 14 (additional information).", kind="names_overflow"))
    return out


def view(graph: FactGraph) -> dict[str, Any] | None:
    """The timeline as the screens show it: each event with its date as MM/DD/YYYY, the current name and why, the other names."""
    data = payload(graph)
    if not data:
        return None
    current = data["current"]
    rows = [{"name": e["name"], "what": e["what"], "date": _us(e.get("date")) or None, "doc": e["doc"], "page": e["page"],
             "from_document": e["tier"] == 1, "setter": e["kind"] in SETTERS, "printed": e["printed"], "current": bool(data.get("settled", True)) and same_name(e["name"], current["name"])
             and e["kind"] not in NOTICES, "uscis": e["kind"] in NOTICES,
             "party": e.get("party"), "parent_key": e.get("parent_key"),
             **({"composition": e["composition"], "given_parent_key": e["given_parent_key"],
                 "given_parent_value": e["given_parent_value"],
                 "given_basis": {key: value for key, value in e["given_basis"].items() if key not in {"reader", "issues"}}}
                if e.get("composition") else {})} for e in data["events"]]
    return {"events": rows, "current": current["name"], "why": data["why"], "others": data["others"], "names": data["names"],
            "marriage_evidence": data.get("marriage_evidence"),
            "settled": bool(data.get("settled", True)), "unordered": bool(data.get("unordered")), "also_seen": data.get("also_seen") or [], "overflow": data.get("overflow") or 0,
            "uscis": [{"name": e["name"], "what": e["what"], "date": _us(e.get("date")) or None, "doc": e["doc"], "page": e["page"]}
                      for e in data["uscis"]],
            "current_doc": {"doc": current["doc"], "page": current["page"], "what": current["what"], "date": _us(current.get("date")) or None},
            # brief K6: the marriage certificate that prints no name after marriage (the card shows it open at its page), and the client's answers
            "question": _question_view(data.get("question")), "client_wrote": data.get("client_wrote") or {},
            # a later name-setting document after a person's choice (the card is open again), and a name a person typed (retyped, never a pick)
            "stale": data.get("stale"), "typed_by_person": current["name"] if current.get("kind") == "person" else None}


def _question_view(question: dict[str, Any] | None) -> dict[str, Any] | None:
    if not question:
        return None
    return {"open": bool(question.get("open")), "kind": question.get("kind") or "certificate", "sentence": question["sentence"],
            "doc": question.get("doc"), "page": question.get("page"), "read_state": question.get("read_state"), "party": question.get("party"),
            "date": _us(question.get("date")) or None, "name": question["name"], "spouse_family": question.get("spouse_family"),
            "spouse_name": question.get("spouse_name")}
