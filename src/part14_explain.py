"""An explanation in Part 14 for every answer the form says to explain (brief L2): the "Explain the Yes answers" card.

WHICH ANSWERS. The form's own lines say which answers need an explanation in Part 14. schemas/firm/part14_wordings.json holds them word for word with
their pages, per edition of Form I-485: the form's page 13 ("If you answer "Yes" to any questions ... provide an explanation of the events and
circumstances in the space provided in Part 14"), and the item lines that add what the explanation must say (items 22 to 41, 42.a to 45, 46,
47 to 55, 50 and 51, 65, 74). The list ("items") is every Part 9 Yes/No answer the product's field map fills, each with the lines it rests on;
its printed item number is read from the edition's own template (src/fill/where.py), never typed. An answer the lines cover but the field map
does not fill (item 63) is listed apart ("not_filled"): the product cannot see it, and docs/attorney_review.md says so.

THE GATE. A packet whose I-485 answers Yes to a listed item is not ready until that item has an explanation an attorney approved for this case,
one line per item: "Part 9, item 14 says Yes and has no explanation in Part 14." (problems, read by src/packet.py).

THE SUGGESTION. For an item the firm ships a wording for, the card suggests it: the firm's DRAFT sentence (schemas/firm/part14_wordings.json
"wordings", every one listed in docs/attorney_review.md) in the office's voice (src/part14_voice.py), its {slots} filled from the case's facts,
each with its source on the card (the document and its page, the court record on the case page and who recorded it, or the client's own
answer). A slot is filled only from a settled fact: one read from a document, or one a person confirmed or set. A slot whose fact is only the
client's own answer stays a blank in square brackets, with the answer beside it for a person to check and use; a slot that cites the arrival
waits while the arrival card is open (src/arrival.py findings); a slot with no fact stays a blank. Nothing else is written: no model writes a
word. A legal citation comes only from a rule that holds the statute's text with its source and the date it was read (src/rules/engine.py
Rule.statute_*); no rule holds one yet, so a wording that would cite the law goes without and the card says the attorney adds the citation.
THE FIRM'S OWN WORDINGS (brief L3, src/wordings.py). Every explanation an attorney approves is kept as a firm wording (its slots abstracted, no value of
the case in it); on a new case the wordings the office approved before for the same item, edition, voice and office are offered above the shipped
one, best fit first (firm_wordings), each with the facts that match, its slots refilled from this case's facts and cited, and "the office wrote
this on N cases". A paralegal picks one (it goes into the text like an edit, and the pick is recorded); an attorney approves it for the case, as
any text. When an attorney switched the local model on, it may re-order the top few by returning a number; it never writes a word.

THE CARD. One entry per item answered Yes. A person edits the text (a review decision: who, when, the old text and the new; "Back to the
suggestion" undoes it); the grammar helper is the declaration's (src/drafting.py: only when an attorney switched it on in Settings, a
suggestion that passes smoothing_allowed, never used until a person accepts it). An attorney approves each entry for this case (a decision,
with Undo); a change to the text after the approval takes the approval back, and the approval holds only while the text, the voice and the
facts the entry was built on are what was approved. The approved text goes on the form's own Part 14 page (src/fill/continuation.py) as one
more entry, its Page, Part and Item from where_is (into_graph, called when the case is read: src/review/state.py reviewed_graph).

What the case keeps: the edits and approvals are review decisions (decisions.json, kind "part14"); part14_explanations.json keeps what each
approval was made over (the text, the voice, the facts), the grammar suggestions and the history of approvals taken back.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import clock
import events
import holders
import schema_path
from holders import OFFICE, producer

SCHEMA = schema_path.path("firm", "part14_wordings")
STATE = "part14_explanations.json"
VERSION = 1
KIND = "part14"  # the review decisions' kind (the Decision log names them by their own titles)
PREFIX = "applicant.part9."
TEXT_MAX = 3000  # characters in one explanation (it continues on copies of the Part 14 page when it is longer than a box)
BLANK = re.compile(r"\[[^\[\]\n]*\]")  # a slot no settled fact filled: "[the date of the judge's order]"
RESULTS = ("Decision: relief granted", "Decision: removal ordered or relief denied", "Removal ordered in absentia",
           "Case terminated or dismissed")  # src/journey.py DECISIONS and TERMINATED: what decides or ends a case in court
TYPED = ("judge_decided", "relief_granted", "how_ended", "ended_date", "court_history", "entry_detention", "other_arrests")  # no record fills these: a person writes them
CITE_NOTE = "the attorney adds the citation"
PICK_NOTE = "Firm wording chosen"  # the note of the decision that puts one of the firm's own wordings (src/wordings.py) into the text
_cache: dict[str, Any] = {}


# -- what ships ------------------------------------------------------------------------------------------------------------------------


def shipped() -> dict[str, Any]:
    """schemas/firm/part14_wordings.json, read again when it changes."""
    stamp = SCHEMA.stat().st_mtime_ns
    if _cache.get("stamp") != stamp:
        _cache.update(stamp=stamp, data=json.loads(SCHEMA.read_text(encoding="utf-8")))
    return _cache["data"]


def edition() -> str | None:
    """The edition of the I-485 the product fills (the template's own)."""
    from fill.where import edition_of

    return edition_of()


def held(ed: str | None = None) -> dict[str, Any] | None:
    """What the product holds for an edition: its lines, the items they cover and the answers it cannot see; None for an edition it does not hold."""
    return (shipped()["forms"]["i485"]["editions"]).get(ed or edition() or "")


def _line_words(spec: dict[str, Any], line: dict[str, Any]) -> str:
    return ("Form I-485" if line["in"] == "form" else "Form I-485 Instructions") + f", page {line['page']}"


def lines_of(item: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The lines an item rests on: {id, where (the form or its instructions, and the page, in words), says (word for word)}."""
    return [{"id": lid, "where": _line_words(spec, spec["lines"][lid]), "says": spec["lines"][lid]["says"], "page": spec["lines"][lid]["page"],
             "in": spec["lines"][lid]["in"]} for lid in item["lines"]]


def spot(key: str, ed: str | None = None) -> tuple[str, str, str]:
    """(page, part, item) where this edition prints the answer (src/fill/where.py); blanks when the template cannot vouch for it."""
    from fill.where import NotFound, where_is

    try:
        return where_is(ed or edition(), key)
    except NotFound:
        return "", "", ""


def question(key: str) -> str:
    """The form's own question for an answer, from its box's tooltip in the template (the I-485's own words), shortened as the cards show it."""
    from fill.where import SCHEMAS, _first_field, _i485_map, _index, _lookup
    from review.state import concise, tidy_tooltip

    spec = _i485_map(str(SCHEMAS)).get(key)
    name = _first_field(spec) if spec else None
    found = _lookup(_index(str(schema_path.path("template", "i485"))), name) if name else None
    if not found:
        return ""
    # the tooltips' own typesetting, closed up: "?: Yes" (the box's side), "1 9 9 7", "I N A. section 2 74 C", "Have you EVER. 44. Do you intend ..."
    q = re.sub(r":\s*(Yes|No)\s*$", "", concise(tidy_tooltip(found[1]), 200))
    q = re.sub(r"(?<=\d) (?=\d)", "", q).replace("I N A.", "INA")
    q = re.sub(r"(\d) ([A-Z]) for\b", r"\1\2 for", q)
    q = re.sub(r"^((?:Have you EVER|Do you intend to)[.:])\s\d{1,2}\.\s?(?:[A-Z]\.?\s)?", r"\1 ", q)
    q = re.sub(r"^Have you EVER\. (?=(?:Do|Are|Have) you)", "", q)
    return re.sub(r"^(Have you EVER|Do you intend to)[.:] ", r"\1: ", q)


_listed: dict[tuple, list[dict[str, Any]]] = {}


def _stamps(ed: str | None) -> tuple:
    """What the list depends on: the edition, the template, the field map and the held lines (each read again when it changes)."""
    from fill.where import SCHEMAS

    return (ed or edition(), (schema_path.path("template", "i485")).stat().st_mtime_ns, schema_path.path("field_map", "i485", SCHEMAS).stat().st_mtime_ns, SCHEMA.stat().st_mtime_ns)


def listed(ed: str | None = None) -> list[dict[str, Any]]:
    """The answers whose Yes needs an explanation in Part 14, in this edition: [{key, page, part, item, question, lines}] in the form's order.
    Worked out once per edition, template, field map and held lines (where_is for 84 answers is a fifth of a second), then kept: a case is
    read many times a minute. Callers read it and never change it."""
    stamp = _stamps(ed)
    if stamp in _listed:
        return _listed[stamp]
    spec = held(stamp[0])
    out = []
    for item in (spec or {}).get("items") or []:
        page, part, number = spot(item["key"], stamp[0])
        out.append({"key": item["key"], "page": page, "part": part, "item": number, "question": question(item["key"]), "lines": lines_of(item, spec)})
    _listed.clear()
    _listed[stamp] = out
    return out


def firm_wordings(key: str, pattern: dict[str, Any], graph=None, client_dir: Path | str | None = None, voice: str = "client", shipped_text: str = "",
                  work: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The firm's own approved wordings for this item and fact pattern, best first (brief L3, the wording library learned from approvals,
    src/wordings.py): the ones the office approved before for the same form, edition, item, voice and office, ranked by the facts true here first,
    then by how close the text is to the shipped one, then by use; each with its slots refilled from this case's facts. They are offered before the
    shipped DRAFT wording. A local model may re-order them when an attorney switched it on (it returns a number), never write one."""
    if graph is None or client_dir is None:
        return []
    import wordings

    return wordings.offers(Path(client_dir), key, pattern, graph, voice, shipped_text, work)["offers"]


def citation(rule_id: str | None) -> dict[str, Any] | None:
    """The citation a wording may carry: only from a rule of the product that holds the statute's text, where it was read and when
    (src/rules/engine.py Rule.statute_*). None otherwise: the sentence goes without it and the card says the attorney adds the citation."""
    import rules

    rule = next((r for r in rules.ALL_RULES if r.rule_id == rule_id), None) if rule_id else None
    if rule is None or not all(str(getattr(rule, f, "") or "").strip() for f in ("statute_cite", "statute_text", "statute_source", "statute_read")):
        return None
    return {"rule": rule.rule_id, "cite": rule.statute_cite, "text": rule.statute_text, "source": rule.statute_source, "read": rule.statute_read}


# -- the case's facts ----------------------------------------------------------------------------------------------------------------------


def _fact(graph, key: str):
    fact = graph.get(key)
    return fact if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def _value(graph, key: str) -> Any:
    fact = _fact(graph, key)
    return fact.value if fact is not None else None


def _settled(fact) -> bool:
    """A fact a slot may be filled from: read from a document (Tier 1) or confirmed or set by a person. The client's own answer (Tier 3) and a
    rule's value (Tier 2) are not, until a person signs them off."""
    return fact is not None and (fact.review is not None or fact.tier == 1)


def _iso(value: Any) -> date | None:
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    try:
        return date.fromisoformat(m.group(0)) if m else None
    except ValueError:
        return None


def us(value: Any) -> str:
    d = value if isinstance(value, date) else _iso(value)
    return d.strftime("%m/%d/%Y") if d else str(value or "")


def _doc_words(doc_type: str | None) -> str:
    try:
        import documents

        return documents.name(doc_type) if doc_type else ""
    except Exception:  # noqa: BLE001 -- a type the taxonomy cannot name is said plainly
        return ""


def _source(fact) -> dict[str, Any]:
    """Where a fact came from, in words, with the document and page when it is one: {words, doc, page}."""
    from factgraph.graph import TYPED_BY_A_PERSON

    if fact.review is not None:
        return {"words": f"Confirmed or set by {fact.review.resolved_by} on a review card", "doc": None, "page": None}
    same = [s for s in fact.sources if s.normalized_value == fact.value] or fact.sources
    paper = next((s for s in same if s.doc_type not in TYPED_BY_A_PERSON and s.doc_type != "derived"), None)
    if paper is not None:
        return {"words": _doc_words(paper.doc_type) or "A document in the folder", "doc": paper.doc_id, "page": paper.page}
    s = same[0] if same else None
    if s is not None and s.doc_type == "office_question":
        return {"words": "The client's answer to the office's question in the portal", "doc": None, "page": None}
    return {"words": "The client's own answer", "doc": s.doc_id if s is not None and s.doc_id not in ("paralegal_review",) else None, "page": None}


def _court(client_dir: Path, nta: bool) -> dict[str, Any]:
    """What the case page records about the immigration court (src/journey.py hearings): every result that decided or ended the case, oldest
    first ({outcome, date (the decision's, when the result holds one), hearing, by, at}), and whether the client is still in proceedings
    (journey.in_court: a Notice to Appear or a hearing, and nothing that ended the case). "Next hearing set" and "Other" decide nothing."""
    import journey

    path = Path(client_dir) / "status.json"
    try:
        status = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        status = {}
    hearings = (status.get("journey") or {}).get("hearings") or []
    found = [(h, h["result"]) for h in hearings if (h.get("result") or {}).get("outcome") in RESULTS]
    found.sort(key=lambda x: (str(x[0].get("date") or ""), str(x[1].get("decision_date") or "")))
    results = [{"outcome": r["outcome"], "date": r.get("decision_date") if _iso(r.get("decision_date")) else None, "hearing": h.get("date"),
                "by": r.get("by"), "at": r.get("at")} for h, r in found]
    return {"results": results, "in_court": journey.in_court(status, nta)}


def _result_words(r: dict[str, Any]) -> str:
    """One result as the card cites it: what the case page records, its date, the hearing, and who recorded it."""
    return (f"The court record on the case page: {r['outcome'].replace('Decision: ', '')}"
            + (f", decided {us(r['date'])}" if r.get("date") else ", no date recorded") + (f", at the hearing of {us(r['hearing'])}" if r.get("hearing") else "")
            + f"; recorded by {r.get('by') or 'a person'} on {us(r.get('at'))}")


def _i360(graph) -> dict[str, Any] | None:
    """The approval notice of the client's Form I-360 in the folder (the latest): {receipt, date, doc}."""
    import journey

    found = [n for n in journey.notices(graph) if n.get("form") == "I-360" and n.get("kind") == "approval" and n.get("receipt")]
    if not found:
        return None
    n = found[-1]
    return {"receipt": n["receipt"], "date": n.get("date"), "doc": n.get("doc")}


def _work(graph) -> dict[str, Any]:
    """The client's job history and the work permits in the case, and the periods of U.S. work outside the permits' dates:
    {jobs, permits, periods, reason, settled}. periods is None when it cannot be worked out (reason says why)."""
    from filing_questions import STATES

    rules_ = shipped()["work"]
    us_names, not_work = set(rules_["us_names"]), set(rules_["not_work"])
    states = set(STATES) | set(STATES.values())
    jobs = []
    current = {p: _fact(graph, f"applicant.employer1_{p}") for p in ("name", "occupation", "city", "state", "country", "date_from", "date_to")}
    lines = [("current job", {("employer" if p == "name" else p): f for p, f in current.items()})]
    for k in range(1, 10):
        lines.append((f"job history, line {k}", {p: _fact(graph, f"questionnaire.prior_employer{k}_{p}")
                                                      for p in ("employer", "occupation", "city", "state", "country", "date_from", "date_to")}))
    for words, facts in lines:
        v = {p: (str(f.value).strip() if f is not None else "") for p, f in facts.items()}
        if not any(v.values()):
            continue
        upper = {x.upper() for x in (v["employer"], v["occupation"]) if x}
        country = v["country"].upper()
        in_us = country in us_names or (not country and v["state"].upper() in states)
        jobs.append({"line": words, "employer": v["employer"] or v["occupation"], "from": _iso(v["date_from"]), "to": _iso(v["date_to"]),
                     "in_us": in_us, "work": not (upper & not_work), "settled": all(_settled(f) for f in facts.values() if f is not None),
                     "doc": next((f.sources[0].doc_id for f in facts.values() if f is not None and f.sources), None)})
    permits = []
    for key in sorted(k for k in graph.all_facts() if re.fullmatch(r"folder\.notice\.[^.]+\.[^.]+\.valid_from", k)):
        start, end = _fact(graph, key), _fact(graph, key[: -len("valid_from")] + "valid_to")
        if start is not None and end is not None and _iso(start.value) and _iso(end.value):
            permits.append({"from": _iso(start.value), "to": _iso(end.value), "doc": start.sources[0].doc_id if start.sources else None})
    card = _fact(graph, "applicant.ead_expiration_date")
    out: dict[str, Any] = {"jobs": jobs, "permits": permits, "periods": None, "reason": "", "settled": False}
    if card is not None and _iso(card.value) and not any(p["to"] == _iso(card.value) for p in permits):
        out["reason"] = (f"The work permit card in the folder shows only its last day ({us(card.value)}); its first day is on the approval notice, which "
                         "is not in the folder. Type the periods.")
        return out
    worked = [j for j in jobs if j["in_us"] and j["work"]]
    if not worked:
        out["reason"] = "No job in the United States is in the client's job history. Type the periods."
        return out
    undated = next((j for j in worked if j["from"] is None), None)
    if undated:
        out["reason"] = f"The client's {undated['line']} ({undated['employer']}) has no start date. Type the periods."
        return out
    today = clock.today()
    periods = []
    for j in sorted(worked, key=lambda j: j["from"]):
        start, end = j["from"], j["to"]
        for p in sorted(permits, key=lambda p: p["from"]):
            if end is not None and p["from"] > end:
                break
            if p["to"] < start:
                continue
            if p["from"] > start:
                periods.append((start, p["from"] - timedelta(days=1), j["employer"]))
            start = max(start, p["to"] + timedelta(days=1))
        if end is None:
            if start <= today:
                periods.append((start, None, j["employer"]))
        elif start <= end:
            periods.append((start, end, j["employer"]))
    if not periods:
        out["reason"] = "Every job in the United States in the client's history falls within the work permit's dates. Check the answer to this item."
        return out
    out["periods"] = "; ".join((f"{us(a)} only" if a == b else f"{us(a)} to {us(b) if b else 'the present'}") + f" ({who})" for a, b, who in periods)
    out["settled"] = all(j["settled"] for j in worked)
    return out


# -- the suggestion ------------------------------------------------------------------------------------------------------------------------


def _pattern(client_dir: Path, graph) -> dict[str, Any]:
    """What the case shows that chooses a wording: the court's record, the I-360's approval, how the client last arrived, the I-94's last day."""
    import arrival
    import journey

    nta = _value(graph, "applicant.nta_present") == "Yes"
    court = _court(Path(client_dir), nta or bool(journey._docs(Path(client_dir)).get("notice_to_appear")))
    manner = _value(graph, arrival.MANNER)
    return {"court": court, "i360": _i360(graph), "ewi": manner == "WITHOUT ADMISSION OR PAROLE", "nta": nta,
            "admit_until": _value(graph, "applicant.i94_admit_until_date"), "arrival_open": _arrival_card(graph)}


def _arrival_card(graph) -> str | None:
    """The title of the arrival card that is open on the case (src/arrival.py findings), as the review screen names it (review/state.py
    _new_item): K6's "confirm the place the notice prints" when the notice's place was read as a port to confirm, else K2's; None when
    no arrival card is open."""
    import arrival

    if not arrival.findings(graph):
        return None
    from review.state import CROSSCHECK_TITLES

    return "Last arrival in the U.S.: confirm the place the notice prints" if arrival.reading(graph) else CROSSCHECK_TITLES[arrival.CITY]


def _only(p: dict[str, Any], outcome: str) -> bool:
    """The case page records exactly one result that decided or ended the case, and it is this one."""
    results = p["court"]["results"]
    return len(results) == 1 and results[0]["outcome"] == outcome


def _holds(when: str, p: dict[str, Any]) -> bool:
    return {"always": True, "only_in_absentia": _only(p, RESULTS[2]), "only_ordered_or_denied": _only(p, RESULTS[1]),
            "only_relief_granted": _only(p, RESULTS[0]), "only_terminated": _only(p, RESULTS[3]),
            "pending": not p["court"]["results"] and p["court"]["in_court"], "i360_approved": p["i360"] is not None,
            "entered_without_inspection_nta": p["ewi"] and p["nta"], "entered_without_inspection": p["ewi"], "admit_until": _iso(p["admit_until"]) is not None}[when]  # a date, never "D/S"


def _slot(name: str, graph, p: dict[str, Any], work: dict[str, Any]) -> dict[str, Any]:
    """One slot: {name, words (what it is), value (the settled fact, written in the text), proposed (the client's own answer, for a person to
    check and use), wait (a card to save first), why (why it is blank), sources: [{words, doc, page}]}."""
    import arrival

    s: dict[str, Any] = {"name": name, "words": shipped()["slots"][name], "value": None, "proposed": None, "wait": None, "why": "", "sources": []}
    if name == "decision_date":
        results = p["court"]["results"]
        s["sources"] = [{"words": _result_words(x), "doc": None, "page": None} for x in results]
        if len(results) == 1 and results[0].get("date"):
            s["value"] = us(results[0]["date"])
        else:
            s["why"] = ("No result of the immigration judge is recorded on the case page (Where the case stands, Immigration court)." if not results
                        else "The case page records no date for this result: type it from the court's order." if len(results) == 1
                        else "The case page records more than one result: write what the court decided, and when.")
        return s
    if name in TYPED:  # what the court decided in words, a termination's date, what happened at the border: no record says it, a person writes it
        court = name not in ("entry_detention", "other_arrests")
        s["sources"] = [{"words": _result_words(x), "doc": None, "page": None} for x in p["court"]["results"]] if court else []
        s["why"] = ("No record says this in words the form can use: write it from the court's order." if court
                    else "No document says this: ask the client and write it (or say there was none).")
        return s
    if name in ("i360_receipt", "i360_approved"):
        n = p["i360"]
        if n is None or (name == "i360_approved" and not n.get("date")):
            s["why"] = "No approval notice for the client's Form I-360 is in the folder." if n is None else "The I-360 approval notice's date was not read."
        else:
            s["value"] = n["receipt"] if name == "i360_receipt" else us(n["date"])
            s["sources"] = [{"words": "The I-360 approval notice" + (" (its notice date)" if name == "i360_approved" else ""), "doc": n.get("doc"), "page": None}]
        return s
    if name == "periods":
        sources = [{"words": f"The client's {j['line']}: {j['employer']}, {us(j['from']) or 'no start date'} to {us(j['to']) if j['to'] else 'the present'}"
                             + ("" if j["in_us"] else " (not in the United States)") + ("" if j["work"] else " (not work)"), "doc": None, "page": None}
                   for j in work["jobs"]]
        sources += [{"words": f"A work permit approval notice: valid {us(x['from'])} to {us(x['to'])}", "doc": x["doc"], "page": None} for x in work["permits"]]
        s["sources"] = sources
        if work["periods"] is None:
            s["why"] = work["reason"]
        elif work["settled"]:
            s["value"] = work["periods"]
        else:
            s["proposed"] = work["periods"]
            s["why"] = "Worked out from the client's own job history, which no person has confirmed: check it, then use it."
        return s
    key = {"arrival_city": arrival.CITY, "arrival_state": arrival.STATE, "arrival_date": arrival.DATE, "admit_until": "applicant.i94_admit_until_date"}[name]
    if name.startswith("arrival_") and p["arrival_open"]:
        s["wait"] = f"The card \"{p['arrival_open']}\" is open: save it first."
        s["why"] = s["wait"]
        return s
    fact = _fact(graph, key)
    if fact is None:
        s["why"] = "Not in the case."
        return s
    shown = us(fact.value) if name in ("arrival_date", "admit_until") else str(fact.value)
    s["sources"] = [_source(fact)]
    if _settled(fact):
        s["value"] = shown
    else:
        s["proposed"] = shown
        s["why"] = "The client's own answer, not confirmed by a person yet: check it, then use it."
    return s


def _fill(text: str, slots: dict[str, dict[str, Any]], cite: str) -> str:
    def put(m: re.Match) -> str:
        name = m.group(1)
        if name == "cite":
            return cite
        s = slots[name]
        return s["value"] if s["value"] else f"[{s['words']}]"

    return re.sub(r"\{(\w+)\}", put, text)


def suggestion(client_dir: Path, graph, key: str, voice: str, p: dict[str, Any] | None = None, work: dict[str, Any] | None = None,
               firm: bool = True) -> dict[str, Any] | None:
    """The suggested explanation for one item: {wording (its id), sentences (the shipped ones used), text (with a bracketed blank for each slot no
    settled fact filled), slots, cite ({held, cite, text, source, read} or {held: False, needed, note}), firm (the firm's own wordings: L3, the
    library's offers, best first; entries() gives them its own way and passes firm=False)}. None when no wording ships for this item and this
    case's facts: the card then asks a person to write it (the firm's own wordings, if it has any, are offered all the same)."""
    p = p if p is not None else _pattern(Path(client_dir), graph)
    data = shipped()
    wording = next((w for w in data["wordings"] if w["item"] == key and _holds(w["when"], p)), None)
    if wording is None:
        return None
    sentences = [wording] + [data["sentences"][n] | {"id": n} for n in wording.get("then") or [] if _holds(data["sentences"][n]["when"], p)]
    names = list(dict.fromkeys(m for s in sentences for m in re.findall(r"\{(\w+)\}", s["text"][voice]) if m != "cite"))
    if "periods" in names and work is None:
        work = _work(graph)
    slots = {n: _slot(n, graph, p, work or {}) for n in names}
    cite_spec = next((s["cite"] for s in sentences if s.get("cite")), None)
    held_cite = citation(cite_spec["rule"]) if cite_spec else None
    cite = ({"held": True} | held_cite) if held_cite else ({"held": False, "needed": cite_spec["needed"], "note": CITE_NOTE} if cite_spec else None)
    words = f" under {held_cite['cite']}" if held_cite else ""
    text = " ".join(_fill(s["text"][voice], slots, words) for s in sentences)
    return {"wording": wording["id"], "sentences": [s["id"] for s in sentences], "text": text, "slots": list(slots.values()), "cite": cite,
            "when": data["when"][wording["when"]], "firm": firm_wordings(key, p, graph, client_dir, voice, text, work) if firm else []}


# -- the case's record -----------------------------------------------------------------------------------------------------------------------


def read(client_dir: Path) -> dict[str, Any]:
    path = Path(client_dir) / STATE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    return {"version": VERSION, "approvals": {}, "smoothing": {}, "history": [], "picks": {}, "ranked": {}} | (data if isinstance(data, dict) else {})


def _save(client_dir: Path, rec: dict[str, Any], action: str, what: str, who: str, role: str | None) -> None:
    rec["version"] = VERSION
    path = Path(client_dir) / STATE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    events.record("decisions", action, what, case_dir=client_dir, who=who, role=role, version=VERSION)


def _need(who: str) -> str:
    who = str(who or "").strip()
    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    return who


def _slug(key: str) -> str:
    return key[len(PREFIX):] if key.startswith(PREFIX) else key


def text_id(key: str) -> str:
    return f"part14-text:{key}"


def approval_id(key: str) -> str:
    return f"part14-approve:{key}"


def _item(key: str, number: str, what: str) -> dict[str, Any]:
    if what == "text":
        return {"id": text_id(key), "kind": KIND, "level": "review", "title": f"The Part 14 explanation for Part 9, item {number}", "group": "check",
                "actions": ["set", "blank"], "facts": [{"key": f"part14_text.{_slug(key)}", "label": f"The Part 14 explanation for Part 9, item {number}",
                                                        "input": {"type": "text", "multiline": True}}]}
    return {"id": approval_id(key), "kind": KIND, "level": "review", "title": f"The attorney's approval of the Part 14 explanation for Part 9, item {number}",
            "group": "attorney", "actions": ["set", "blank"], "facts": [{"key": f"part14_approved.{_slug(key)}", "label": f"The attorney's approval of the Part 14 "
                                                                                                                         f"explanation for Part 9, item {number}",
                                                                          "input": {"type": "text", "multiline": True}}]}


def _live(log: dict[str, dict[str, Any]], iid: str) -> dict[str, Any] | None:
    d = log.get(iid)
    return d if d and not d.get("undone") and d.get("action") == "set" else None


def _person(d: dict[str, Any] | None) -> dict[str, Any] | None:
    if not d:
        return None
    return {"who": d.get("reviewer") or d.get("by"), "role": d.get("role"), "at": d.get("at"), "date": clock.us_date(d.get("at"))}


# -- the entries ------------------------------------------------------------------------------------------------------------------------------


def answered_yes(graph, ed: str | None = None) -> list[dict[str, Any]]:
    """The listed items this case answers Yes."""
    return [x for x in listed(ed) if _value(graph, x["key"]) == "Yes"]


def entries(client_dir: Path, graph, log: dict[str, dict[str, Any]] | None = None, voice: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One entry per listed item the case answers Yes: the form's question and where it sits, the lines it rests on, the suggestion with its
    slots, the text as it stands (a person's edit, else the suggestion), the edits, the approval and whether it still holds, and what it waits on."""
    import part14_voice
    import wordings
    from review.state import _history, load_decision_log

    client_dir = Path(client_dir)
    yes = answered_yes(graph)
    if not yes:
        return []
    log = log if log is not None else load_decision_log(client_dir)
    v = voice or part14_voice.voice(client_dir)
    rec = read(client_dir)
    p = _pattern(client_dir, graph)
    work = None
    out = []
    yes_keys = [x["key"] for x in yes]
    for x in yes:
        key = x["key"]
        if work is None and any(w["item"] == key and "{periods}" in w["text"]["client"] for w in shipped()["wordings"]):
            work = _work(graph)
        s = suggestion(client_dir, graph, key, v["voice"], p, work, firm=False)
        firm = wordings.offers(client_dir, key, p, graph, v["voice"], s["text"] if s else "", work, yes_keys)  # the firm's own wordings, best first (brief L3)
        if s:
            s["firm"] = firm["offers"]
        edit = _live(log, text_id(key))
        text = str(next(iter(edit["values"].values()))) if edit else (s["text"] if s else "")
        picked = rec["picks"].get(key)
        if picked and not (edit or text == picked.get("text")):
            picked = None  # back to the suggestion: the wording picked is no longer in the text
        pick_waits = []
        if picked:
            held = wordings.get(wordings.case_root(client_dir), picked["wording"])
            pick_waits = wordings.refill(held, graph, p, work)["waits"] if held else []
        approved = _live(log, approval_id(key))
        facts = {sl["name"]: sl["value"] or sl["proposed"] for sl in (s["slots"] if s else [])}
        approval = None
        if approved:
            made = rec["approvals"].get(key) or {}
            approved_text = str(next(iter(approved["values"].values())))
            before = made.get("facts") or {}
            moved = [shipped()["slots"].get(n, n) for n in sorted(set(facts) | set(before)) if before.get(n) != facts.get(n)] if made else []
            if made and made.get("voice") != v["voice"]:  # the most telling reason first: a new voice or new facts also change the suggested text
                why = "The voice for Part 14 entries changed after the approval: write it in that voice and approve it again."
            elif moved:
                why = "The facts it is built on changed after the approval: " + ", ".join(moved) + "."
            elif approved_text != text:
                why = "The explanation changed after the attorney approved it."
            else:
                why = ""
            approval = _person(approved) | {"text": approved_text, "holds": not why, "why_not": why, "wording": made.get("wording"), "library": made.get("library")}
        waits = list(dict.fromkeys([sl["wait"] for sl in (s["slots"] if s else []) if sl.get("wait")] + pick_waits))  # one card named once, however many slots wait on it
        chain = _history(log.get(text_id(key)))
        smooth = rec["smoothing"].get(key)
        state = ("approved" if approval and approval["holds"] else "changed" if approval else "waiting" if waits
                 else "blanks" if BLANK.search(text) else "empty" if not text.strip() else "draft")
        out.append({
            "key": key, "page": x["page"], "part": x["part"], "item": x["item"], "question": x["question"], "lines": x["lines"],
            "suggestion": s, "text": text, "how": "edited" if edit else "suggested" if s else "none", "firm": firm, "pick": picked,
            "edit": (_person(edit) | {"old": edit.get("old"), "suggestion": edit.get("note") == _smoothing_note(), "firm_wording": edit.get("note") == PICK_NOTE}) if edit else None,
            "edits": [_person(h) | {"old": h.get("old"), "new": next(iter((h.get("values") or {}).values()), None), "undone": _person(h["undone"]) if h.get("undone") else None,
                                    "suggestion": h.get("note") == _smoothing_note(), "firm_wording": h.get("note") == PICK_NOTE} for h in chain],
            "approval": approval, "facts": facts, "waits": waits, "blanks": BLANK.findall(text), "state": state, "voice": v["voice"],
            "smoothing": smooth if smooth and smooth.get("before") == text else None,
        })
    return out


def _smoothing_note() -> str:
    from drafting import SUGGESTION_NOTE

    return SUGGESTION_NOTE


@producer(OFFICE)
def problems_of(ents: list[dict[str, Any]]) -> list[str]:
    """The gate's lines, one per item, in words. An explanation approved and since changed, or written and waiting, is the attorney's (src/approvals.py lists it);
    an item with no explanation yet is the office's to write."""
    out = []
    for e in ents:
        a = e["approval"]
        if a and a["holds"]:
            continue
        if a:
            out.append(holders.held(holders.ATTORNEY, f"Part 9, item {e['item']} says Yes and its explanation in Part 14 is no longer approved: "
                                    f"{a['why_not'][:1].lower()}{a['why_not'][1:]} An attorney approves it again on the Explain the Yes answers card.", via="part14"))
        else:
            line = f"Part 9, item {e['item']} says Yes and has no explanation in Part 14."
            out.append(holders.held(holders.ATTORNEY, line, via="part14") if e["state"] == "draft" else line)
    return out


@producer(OFFICE)
def problems(client_dir: Path, graph=None) -> list[str]:
    """What keeps a packet with this case's I-485 from being ready: every listed item answered Yes without an approved explanation, one line each
    (and one line when the product does not hold the lines of the edition it fills)."""
    client_dir = Path(client_dir)
    if graph is None:
        if not (client_dir / "fact_graph.json").exists():
            return []
        from review.state import reviewed_graph

        graph = reviewed_graph(client_dir)
    return _not_held() or problems_of(entries(client_dir, graph))


def _not_held() -> list[str]:
    ed = edition()
    if held(ed) is None:
        return [f"The lines that say which answers need an explanation in Part 14 are not held for the {ed} edition of Form I-485 yet: the "
                "explanations cannot be checked until they are."]
    return []


# -- what people do on the card ------------------------------------------------------------------------------------------------------------


def _entry(client_dir: Path, key: str) -> dict[str, Any]:
    from review.state import reviewed_graph

    if not (Path(client_dir) / "fact_graph.json").exists():
        raise LookupError("This case has not been read yet: there is no I-485 to explain.")
    e = next((x for x in entries(client_dir, reviewed_graph(client_dir)) if x["key"] == key), None)
    if e is None:
        raise LookupError("That answer is not a Yes on this case's I-485 that needs an explanation.")
    return e


def _take_back(client_dir: Path, e: dict[str, Any], who: str, role: str | None, why: str) -> None:
    """An approval taken back (by a change to the text, or by hand): the decision is undone, and the history says who, when and why."""
    from review.state import load_decision_log, undo_decision

    if not _live(load_decision_log(Path(client_dir)), approval_id(e["key"])):
        return
    undo_decision(Path(client_dir), approval_id(e["key"]), who, role, lifted=f"Took back the approval of the Part 14 explanation for Part 9, item {e['item']}: {why}")
    rec = read(client_dir)
    rec["history"].append({"key": e["key"], "item": e["item"], "approval": e["approval"], "taken_back": {"who": who, "role": role, "at": clock.stamp()}, "why": why})
    rec["approvals"].pop(e["key"], None)
    _save(client_dir, rec, "taken_back", f"The approval of the Part 14 explanation for Part 9, item {e['item']} was taken back", who, role)
    import wordings

    wordings.withdraw(client_dir, e["key"], who, role)  # the firm's wording kept from it stops counting this case (brief L3)


def edit(client_dir: Path, key: str, text: str, who: str, role: str | None = None, note: str = "Part 14 explanation edited") -> dict[str, Any]:
    """A person's text for one explanation: a review decision (who, when, the old text and the new). A change after the approval takes it back."""
    from review.state import record_decision

    who = _need(who)
    text = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines())
    if not text:
        raise ValueError("Write the explanation first (or use Back to the suggestion).")
    if len(text) > TEXT_MAX:
        raise ValueError(f"The explanation is longer than {TEXT_MAX:,} characters: shorten it.")
    e = _entry(client_dir, key)
    if text == e["text"]:
        raise ValueError("Nothing changed in this explanation.")
    record_decision(Path(client_dir), _item(key, e["item"], "text"), {"action": "set", "values": {f"part14_text.{_slug(key)}": text}, "reviewer": who,
                                                                       **({"role": role} if role else {}), "note": note, "old": e["text"]})
    if e["approval"]:
        _take_back(client_dir, e, who, role, "the text changed after the approval")
    return view(client_dir, role)


def revert(client_dir: Path, key: str, who: str, role: str | None = None) -> dict[str, Any]:
    """Back to the suggestion: the edit is marked undone (kept on file with who and when); an approval of the edited text is taken back."""
    from review.state import undo_decision

    who = _need(who)
    e = _entry(client_dir, key)
    if not e["edit"]:
        raise ValueError("This explanation is the suggestion already.")
    undo_decision(Path(client_dir), text_id(key), who, role)
    rec = read(client_dir)
    if rec["picks"].pop(key, None) is not None:  # back to the suggestion: the firm wording that was picked is not in the text any more
        _save(client_dir, rec, "unpicked", f"Went back to the suggestion for Part 9, item {e['item']}: the firm wording chosen is no longer used", who, role)
    if e["approval"]:
        _take_back(client_dir, e, who, role, "the text went back to the suggestion")
    return view(client_dir, role)


def approve(client_dir: Path, key: str, who: str, role: str | None = None, slots: dict[str, str] | None = None) -> dict[str, Any]:
    """The attorney approves this explanation for this case, exactly as it reads now: a decision (who, when, the text), undone with Undo. Refused
    while a slot is a bracketed blank or waits on another card. The approved text is kept as one of the firm's own wordings (src/wordings.py), its
    slots abstracted: slots says, for a date, place, name or number the card offered ("make this a slot?"), "keep" to leave it as text (the
    default is a slot); the client's own identifiers are slots whatever it says."""
    import wordings
    from review.state import record_decision

    who = _need(who)
    if role == "paralegal":
        raise PermissionError("Only an attorney approves an explanation for Part 14.")
    e = _entry(client_dir, key)
    if not e["text"].strip():
        raise ValueError("There is no explanation to approve yet: write it first.")
    if e["waits"]:
        raise ValueError(e["waits"][0])
    if e["blanks"]:
        raise ValueError(f"Fill every blank in square brackets first ({e['blanks'][0]}), or take it out.")
    if e["approval"] and e["approval"]["holds"]:
        raise ValueError("This explanation is approved already.")
    stored = record_decision(Path(client_dir), _item(key, e["item"], "approve"), {"action": "set", "values": {f"part14_approved.{_slug(key)}": e["text"]},
                                                                                  "reviewer": who, **({"role": role} if role else {}), "note": "approved for this case"})
    library = wordings.learn(client_dir, e, who, role, stored["at"], choices=slots)
    rec = read(client_dir)
    rec["approvals"][key] = {"item": e["item"], "text": e["text"], "voice": e["voice"], "facts": e["facts"],
                             "wording": (e["suggestion"] or {}).get("wording") if e["how"] == "suggested" else None,
                             "edited": e["how"] == "edited", "by": who, "role": role, "at": stored["at"], "library": library}
    _save(client_dir, rec, "approved", f"Approved the Part 14 explanation for Part 9, item {e['item']}", who, role)
    return view(client_dir, role)


def pick(client_dir: Path, key: str, wording_id: str, who: str, role: str | None = None) -> dict[str, Any]:
    """A person chooses one of the firm's own wordings offered for this item: its text, with this case's facts in its slots, goes into the explanation
    like an edit (a review decision: who, when, the old text and the new), and the pick is recorded. An attorney approves it for the case as any text;
    an approval already given is taken back by the change."""
    from review.state import record_decision

    who = _need(who)
    e = _entry(client_dir, key)
    offer = next((o for o in e["firm"]["offers"] if o["id"] == wording_id), None)
    if offer is None:
        raise ValueError("That wording is not one the firm offers for this answer on this case.")
    text = offer["text"]
    if len(text) > TEXT_MAX:
        raise ValueError(f"The explanation is longer than {TEXT_MAX:,} characters: shorten it.")
    if text != e["text"]:
        record_decision(Path(client_dir), _item(key, e["item"], "text"), {"action": "set", "values": {f"part14_text.{_slug(key)}": text}, "reviewer": who,
                                                                           **({"role": role} if role else {}), "note": PICK_NOTE, "old": e["text"]})
        if e["approval"]:
            _take_back(client_dir, e, who, role, "a firm wording was chosen after the approval")
    rec = read(client_dir)
    rec["picks"][key] = {"wording": wording_id, "text": text, "by": who, "role": role, "at": clock.stamp()}
    _save(client_dir, rec, "picked", f"Chose a firm wording for Part 9, item {e['item']}", who, role)
    return view(client_dir, role)


def keep_preview(client_dir: Path, key: str) -> dict[str, Any]:
    """What the library would keep of this explanation if it were approved now: the text with its slots, and the dates, places, names and numbers it
    offers to make slots ("make this a slot?"). Nothing is written."""
    import wordings

    e = _entry(client_dir, key)
    if not e["text"].strip():
        raise ValueError("There is no explanation to approve yet: write it first.")
    return wordings.preview(client_dir, e)


def rank(client_dir: Path, key: str, who: str, role: str | None = None, model=None) -> dict[str, Any]:
    """The local model puts the firm's top wordings in order (a number, never a word): only when an attorney switched it on (src/wordings.py rank)."""
    import wordings

    wordings.rank(Path(client_dir), key, who, role, model)
    return view(client_dir, role)


def undo(client_dir: Path, key: str, who: str, role: str | None = None) -> dict[str, Any]:
    """Takes the approval back by hand (an attorney): the explanation is not approved until an attorney approves it again."""
    who = _need(who)
    if role == "paralegal":
        raise PermissionError("Only an attorney takes back the approval of an explanation.")
    e = _entry(client_dir, key)
    if not e["approval"]:
        raise ValueError("This explanation is not approved.")
    _take_back(client_dir, e, who, role, "taken back by hand")
    return view(client_dir, role)


def after_undo(client_dir: Path, item_id: str, who: str, role: str | None = None) -> None:
    """The Decision log's Undo reopened one of this tab's decisions (review.state.undo_decision, called by the app's /api/undo): the approval or the text
    behind it. What was approved is then not approved any more, so the case's record of the approval is closed and the firm's wording kept from it stops
    counting this case (src/wordings.py withdraw), as when the tab itself takes the approval back. A text undone after an approval takes the approval back
    too (the entry is no longer what was approved), and a picked firm wording is no longer in the text."""
    import wordings
    from review.state import load_decision_log, undo_decision

    if not item_id.startswith(("part14-approve:", "part14-text:")):
        return
    key = item_id.split(":", 1)[1]
    client_dir = Path(client_dir)
    log = load_decision_log(client_dir)
    rec = read(client_dir)
    if item_id.startswith("part14-text:") and rec["picks"].pop(key, None) is not None and not _live(log, text_id(key)):
        _save(client_dir, rec, "unpicked", "Reopened the explanation: the firm wording chosen is no longer in the text", who, role)
        rec = read(client_dir)
    made = rec["approvals"].get(key)
    if made is None:
        return
    if _live(log, approval_id(key)):  # the text went back to the suggestion while the approval stood: it is no longer what was approved
        undo_decision(client_dir, approval_id(key), who, role, lifted=f"Took back the approval of the Part 14 explanation for Part 9, item {made.get('item')}: the text was reopened")
        rec = read(client_dir)
    rec["history"].append({"key": key, "item": made.get("item"), "approval": made, "taken_back": {"who": who, "role": role, "at": clock.stamp()},
                           "why": "reopened in the Decision log"})
    rec["approvals"].pop(key, None)
    _save(client_dir, rec, "taken_back", f"The approval of the Part 14 explanation for Part 9, item {made.get('item')} was taken back", who, role)
    wordings.withdraw(client_dir, key, who, role)


def smooth(client_dir: Path, key: str, who: str, role: str | None = None, model=None) -> dict[str, Any]:
    """The grammar helper, as the declaration has it (src/drafting.py): only when an attorney switched it on in Settings; the local model's
    suggestion is shown only if it passes drafting.smoothing_allowed, and is never used until a person accepts it."""
    import drafting

    who = _need(who)
    if not drafting.smoothing_on():
        raise ValueError("Grammar smoothing is off. An attorney can switch it on in Settings (Drafting and models).")
    e = _entry(client_dir, key)
    if not e["text"].strip():
        raise ValueError("There is no explanation to check yet.")
    try:
        after, engine = (model or drafting.smooth_with_model)(e["text"])
    except Exception:  # noqa: BLE001 -- the model failing is said on the card; the text stays as it was
        after, engine = "", drafting.MODEL_UNREACHABLE
    ok, why = drafting.smoothing_allowed(e["text"], after)
    rec = read(client_dir)
    rec["smoothing"][key] = {"before": e["text"], "after": after[:TEXT_MAX], "accepted": ok, "reason": why, "engine": engine,
                             "by": {"who": who, "role": role, "at": clock.stamp()}, "diff": drafting.diff(e["text"], after) if after else [],
                             "same": after.strip() == e["text"].strip()}
    _save(client_dir, rec, "smoothed", f"Asked for a grammar suggestion on the Part 14 explanation for Part 9, item {e['item']}", who, role)
    return view(client_dir, role)


def accept_smoothing(client_dir: Path, key: str, who: str, role: str | None = None) -> dict[str, Any]:
    """A person takes the grammar suggestion: recorded like an edit, and undone the same way."""
    import drafting

    who = _need(who)
    e = _entry(client_dir, key)
    s = e["smoothing"]
    if not s or not s.get("accepted") or s.get("declined") or s.get("same"):
        raise ValueError("There is no grammar suggestion to take for this explanation.")
    return edit(client_dir, key, s["after"], who, role, note=drafting.SUGGESTION_NOTE)


def decline_smoothing(client_dir: Path, key: str, who: str, role: str | None = None) -> dict[str, Any]:
    who = _need(who)
    e = _entry(client_dir, key)
    rec = read(client_dir)
    if not rec["smoothing"].get(key):
        raise ValueError("There is no grammar suggestion for this explanation.")
    rec["smoothing"][key]["declined"] = {"who": who, "role": role, "at": clock.stamp()}
    _save(client_dir, rec, "declined", f"Turned down the grammar suggestion on the Part 14 explanation for Part 9, item {e['item']}", who, role)
    return view(client_dir, role)


# -- the card, the form and the bundle ------------------------------------------------------------------------------------------------------


def view(client_dir: Path, role: str | None = None) -> dict[str, Any]:
    """The "Explain the Yes answers" card: the office's voice and its note, one entry per Yes that needs an explanation, what holds the packet."""
    import drafting
    import part14_voice

    client_dir = Path(client_dir)
    v = part14_voice.voice(client_dir)
    ed = edition()
    spec = held(ed)
    base = {"voice": v, "edition": ed, "held": spec is not None, "can_approve": role != "paralegal", "smoothing_on": drafting.smoothing_on(),
            "general": lines_of({"lines": ["form_p13", "instr_p5"]}, spec) if spec else [],
            "not_filled": [{"question": n["question"], "page": n["page"], "why": n["why"], "lines": lines_of(n, spec)} for n in spec["not_filled"]] if spec else [],
            "firm_wordings": ("The firm's own wordings: every explanation an attorney approves is kept, with its slots in place of the case's facts, and offered first on a "
                              "later case with the same facts. Nothing is used until a person picks it and an attorney approves it for the case.")}
    if not (client_dir / "fact_graph.json").exists():
        return base | {"entries": [], "problems": [], "read": False}
    from review.state import reviewed_graph

    graph = reviewed_graph(client_dir)
    ents = entries(client_dir, graph, voice=v)
    return base | {"entries": ents, "problems": _not_held() or problems_of(ents), "read": True}


def into_graph(graph, client_dir: Path, decisions: dict[str, dict[str, Any]]):
    """The approved explanations as Part 14 entries of the I-485 (the facts applicant.p14_block<n>_page/part/item/text, after the ones the
    questionnaire made): only an approval that still holds, in the form's order, each with the Page, Part and Item where this edition prints the
    answer. Each entry is the attorney's decision (signed off: no review card asks about it again). Called when the case is read."""
    if not any(iid.startswith("part14-approve:") for iid in decisions):
        return graph
    try:
        from review.state import load_decision_log

        ents = [e for e in entries(Path(client_dir), graph, load_decision_log(Path(client_dir))) if e["approval"] and e["approval"]["holds"]]
    except Exception as exc:  # noqa: BLE001 -- the case still reads; the packet's gate reads the explanations again and says what is wrong
        import sys

        sys.stderr.write(f"Part 14 explanations not placed ({type(exc).__name__}: {exc})\n")
        return graph
    n = 1
    while graph.get(f"applicant.p14_block{n}_text") is not None:
        n += 1
    for e in ents:
        a = e["approval"]
        raw = f"the explanation for Part 9, item {e['item']}, approved by {a['who']} on {a['date']}"
        for part, value in (("page", e["page"]), ("part", e["part"]), ("item", e["item"]), ("text", a["text"])):
            if not value:
                continue  # a spot the template cannot vouch for stays blank, and is flagged for a person (batch.part14_spot_flags)
            k = f"applicant.p14_block{n}_{part}"
            graph.add_source(k, "approved explanation", "paralegal_review", raw, value, 1.0, tier=3)
            graph.set_by_review(k, value, a["who"], raw)
        n += 1
    return graph


def bundle_rows(client_dir: Path) -> dict[str, Any] | None:
    """Each explanation for the review bundle: the item and the lines it rests on, the text, every slot's value and source, the edits and the
    approval. None when the case answers no listed item Yes."""
    client_dir = Path(client_dir)
    if not (client_dir / "fact_graph.json").exists():
        return None
    from review.state import reviewed_graph

    graph = reviewed_graph(client_dir)
    ents = entries(client_dir, graph)
    if not ents:
        return None
    import part14_voice

    return {"entries": [{k: v for k, v in e.items() if k != "firm"} for e in ents], "voice": part14_voice.voice(client_dir), "history": read(client_dir)["history"]}
