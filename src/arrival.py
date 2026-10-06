"""The client's last arrival, settled once for every form and letter (brief K2).

Three kinds of source say where, when and how the client last arrived in the United States:
  1. the I-94 (a CBP record of an admission): its own arrival date is exact, and it outranks the "on or about" date of a paper below;
  2. a DHS paper in the folder: the Notice to Appear (its allegation "You arrived in the United States at or near CITY, ST on or
     about DATE", the box and the allegation about admission or parole) or an I-213: a government document, Tier 1, which outranks
     what the client wrote (docs/GRAPH_MODEL.md: a document outranks the client's statement);
  3. the client's own answers (the questionnaire's place, date and way of entering): Tier 3, used only when nothing above says it.

What this module settles are the facts every reader uses: applicant.last_arrival_city and applicant.last_arrival_state (Part 1 item
10), applicant.last_arrival_date (item 10, the settled date: the I-94's, else a DHS paper's, else the client's) and
applicant.last_arrival_manner (item 11, from a DHS paper when there is no I-94). The client's own answers stay on their facts as
sources, never deleted: where they differ from the paper, the value used is the paper's and a review card (kind "crosscheck", keyed
applicant.last_arrival_city) shows both with both documents, the product's pick and why. A reviewer who saved the card has decided:
the settling leaves those boxes alone.

An allegation that does not match the standard wording (src/extract/arrival.py) gives no city, state or date: its text is shown on the
card and nothing is filled from it. A port is never guessed.

A notice that prints a city with a state that is not a state (brief K6: a real notice read by OCR as "at or near SAN LUIS, Ad") gives no
city or state either. Its place is kept exactly as printed (nta.arrival_text), and when the city is a port of entry in exactly one state in
the product's own port table (schemas/geo/ports_of_entry.json, copied from CBP's "Locate a Port of Entry" pages by
tools/ports_of_entry.py), the card offers that state as the product's reading: "The notice prints "SAN LUIS, Ad". San Luis is a port of
entry in Arizona. Confirm or correct." The reading is a suggestion in the card's boxes, never a value: Part 1 item 10's city and state stay
empty (the client's other answer is kept as a source, not used) until a person saves the card, the card is open and the packet waits on it.
A city that names a port in two states (Portland, Columbus ...) or in none gives no reading, and the printed token is never read as a state
by itself ("Ad" is never taken for AZ because the letters are close). The other way round (verification of K6): a notice that prints a
real state, but a city that is a port of entry in exactly one OTHER state ("SAN LUIS, Al": San Luis is a port only in Arizona), fills
nothing at Tier 1 either; the card names both states and a person types the one the notice means.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from factgraph import FactGraph
from factgraph.graph import TYPED_BY_A_PERSON, Resolution
import schema_path

PORTS = schema_path.path("geo", "ports_of_entry")

# the papers that state an arrival, best first: (name in a sentence, fact prefix, the fact for each part)
PAPERS = (
    ("the Notice to Appear", "nta", {"city": "nta.arrival_city", "state": "nta.arrival_state", "place": "nta.arrival_place",
                                    "date": "nta.arrival_date", "qualifier": "nta.arrival_date_qualifier", "manner": "nta.arrival_manner",
                                    "text": "nta.arrival_text", "count": "nta.arrival_count"}),
    ("the Record of Deportable/Inadmissible Alien (I-213)", "i213", {"city": "i213.entry_city", "state": "i213.entry_state", "place": "i213.entry_place",
                                                                    "date": "i213.entry_date", "qualifier": "i213.entry_date_qualifier",
                                                                    "manner": "i213.entry_manner", "text": "", "count": ""}),
)
# the box on the I-485's item 11 for each manner a DHS paper can print (an "arriving alien" is neither: a person decides)
ITEM_11 = {"present without admission or parole": "WITHOUT ADMISSION OR PAROLE", "admitted but removable": "ADMITTED", "admitted": "ADMITTED",
           "paroled": "PAROLED"}
MANNER_WORDS = {"WITHOUT ADMISSION OR PAROLE": "present without admission or parole", "ADMITTED": "admitted", "PAROLED": "paroled",
                "OTHER": "other"}
CITY, STATE, DATE, MANNER = ("applicant.last_arrival_city", "applicant.last_arrival_state", "applicant.last_arrival_date",
                             "applicant.last_arrival_manner")
SELF_DATE = "applicant.last_arrival_date_self_reported"
KEYS = (CITY, STATE, DATE, MANNER)
RULE = "ARRIVAL-01"


def _value(graph: FactGraph, key: str) -> Any:
    fact = graph.get(key) if key else None
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def _decided(graph: FactGraph, key: str) -> bool:
    fact = graph.get(key)
    return fact is not None and fact.review is not None


def _typed(graph: FactGraph, key: str) -> list[str]:
    """What the client (or a reviewer's typing) said for this fact: the values of its typed sources, in order."""
    fact = graph.get(key)
    if fact is None:
        return []
    return list(dict.fromkeys(str(s.normalized_value) for s in fact.sources if s.doc_type in TYPED_BY_A_PERSON and s.normalized_value not in (None, "")))


def _us(iso: str | None) -> str:
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if iso and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(iso)) else str(iso or "")


def _norm(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def papers(graph: FactGraph) -> list[dict[str, Any]]:
    """The DHS papers in the folder that state something about the arrival, best first, each as {name, prefix, parts: {part: value}}."""
    out = []
    for name, prefix, keys in PAPERS:
        parts = {part: _value(graph, key) for part, key in keys.items() if key}
        if any(parts.get(p) for p in ("city", "place", "date", "manner", "text")):
            out.append({"name": name, "prefix": prefix, "keys": keys, "parts": parts})
    return out


@lru_cache(maxsize=1)
def _ports() -> dict[str, dict[str, list[str]]]:
    """{place word: {state code: [the port's place as CBP prints it]}}, from the product's own port table. An empty or missing table gives
    no reading at all (the rule then keeps the printed text only, as before K6)."""
    try:
        table = json.loads(PORTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    for port in table.get("ports") or []:
        for n, place in enumerate(port.get("places") or []):
            printed = [p.strip() for p in re.sub(r"\([^)]*\)", " ", port["name"]).split(",")]
            words = next((p for p in printed if re.sub(r"[^A-Z]", "", p.upper()) == re.sub(r"[^A-Z]", "", place)), place.title())
            out.setdefault(place, {}).setdefault(port["state"], []).append(words)
    return out


def port_states(city: str) -> dict[str, list[str]]:
    """{state code: [how CBP prints the place]} for every state whose ports the city names, in the product's port table."""
    from extract.names import fold_name

    return _ports().get(fold_name(city or ""), {})


def state_words(code: str) -> str:
    """"AZ" -> "Arizona" (the state's own name, as on USPS's list)."""
    from extract.arrival import STATE_NAMES

    name = next((n for n, c in STATE_NAMES.items() if c == code), code)
    return " ".join(w.lower() if w == "OF" else w.capitalize() for w in name.split())


def _as_printed(graph: FactGraph, paper: dict[str, Any]) -> str | None:
    """The place exactly as the paper prints it ("SAN LUIS, Al", its lower case kept: that is the clue a person needs), read again from the
    allegation the place fact was taken from; None when that sentence is not there."""
    from extract.arrival import read_arrival

    fact = graph.get(paper["keys"]["place"])
    for s in fact.sources if fact is not None else []:
        found = read_arrival(str(s.raw_value or ""))
        if found is not None and found.place.text:
            return found.place.text
    return None


def reading(graph: FactGraph) -> dict[str, Any] | None:
    """The product's reading of a notice's place whose state is not a state (brief K6): {paper, printed, city, state, words, state_words},
    or None. Only the Notice to Appear's one arrival, printed in the standard wording as "CITY, TOKEN" with a TOKEN that is no state, and
    only when the city is a port of entry in exactly one state of the port table. None once a person saved the place, or when any paper
    prints a real city and state."""
    from extract.arrival import city_and_token

    if _decided(graph, CITY) or _decided(graph, STATE):
        return None
    found = papers(graph)
    paper = next((p for p in found if p["prefix"] == "nta"), None)
    if paper is not None and paper["parts"].get("city") and paper["parts"].get("state"):
        # a real state, but the city is a port of entry in exactly one OTHER state ("SAN LUIS, Al": San Luis is a port only in Arizona): the
        # printed state may be misread, so nothing is filled from it and the card shows both (verification of K6)
        city, printed_state = str(paper["parts"]["city"]), str(paper["parts"]["state"])
        states = port_states(city)
        if len(states) == 1 and printed_state not in states:
            code, words = next(iter(states.items()))
            printed = _as_printed(graph, paper) or f"{city}, {printed_state}"
            return {"paper": paper["name"], "printed": printed, "printed_token": printed.rsplit(",", 1)[-1].strip(" ."), "city": city, "state": code,
                    "words": words[0], "state_words": state_words(code), "mismatch": True, "printed_state": printed_state,
                    "printed_state_words": state_words(printed_state)}
        return None
    if any(p["parts"].get("city") and p["parts"].get("state") for p in found):
        return None
    if paper is None or not paper["parts"].get("place") or str(paper["parts"].get("count") or "1") != "1":
        return None  # no standard allegation, or more than one arrival: a person chooses (findings says so)
    printed = str(paper["parts"].get("text") or paper["parts"]["place"])
    shape = city_and_token(printed)
    if shape is None:
        return None
    states = port_states(shape[0])
    if len(states) != 1:
        return None  # a port in two states (or in none): the printed text only
    code, words = next(iter(states.items()))
    return {"paper": paper["name"], "printed": printed, "city": shape[0], "state": code, "words": words[0], "state_words": state_words(code)}


def _copy(graph: FactGraph, from_key: str, to_key: str, value: Any = None, confidence: float = 0.95) -> Any:
    """The paper's own source of `from_key`, added to `to_key` as Tier 1 with its page and raw line (a source is never retyped).
    value: what the box takes when it is not the paper's own wording ("present without admission or parole" is box 11.c).
    Returns the value the box takes."""
    fact = graph.get(from_key)
    if fact is None or not fact.sources:
        return None
    s = fact.sources[0]
    value = s.normalized_value if value is None else value
    graph.add_source(to_key, s.doc_id, s.doc_type, s.raw_value, value, confidence, tier=1, from_facts=[from_key], page=s.page)
    return value


def _take(graph: FactGraph, key: str, from_key: str, why: str, value: Any = None) -> None:
    """Puts the paper's value in `key` and, if the client's answer disagrees, settles the conflict in the paper's favor (both stay as sources)."""
    if _decided(graph, key):
        return  # a person saved the card: what they chose stands
    taken = _copy(graph, from_key, key, value)
    fact = graph.get(key)
    if fact is not None and fact.status == "conflict":
        graph.resolve_conflict(key, taken, why, RULE)


def settle_date(graph: FactGraph, found: list[dict[str, Any]] | None = None) -> None:
    """Part 1 item 10's date, the one every form prints (the I-485, and every companion form's "date of last entry"): the I-94's own arrival
    date (exact), else a DHS paper's ("on or about"), else the client's. A companion form filled from a graph that was not assembled (a
    test's, or one saved before this fact existed) asks for it here."""
    if _decided(graph, DATE):
        return
    if _value(graph, "applicant.i94_arrival_date"):
        _copy(graph, "applicant.i94_arrival_date", DATE)
        return
    dated = next((p for p in (papers(graph) if found is None else found) if p["parts"].get("date")), None)
    if dated:
        _copy(graph, dated["keys"]["date"], DATE)
    elif _value(graph, SELF_DATE):
        fact = graph.get(SELF_DATE)
        s = fact.sources[0]
        graph.add_source(DATE, s.doc_id, s.doc_type, s.raw_value, s.normalized_value, s.confidence, tier=3, from_facts=[SELF_DATE])


def _placed(found: list[dict[str, Any]], read: dict[str, Any] | None) -> dict[str, Any] | None:
    """The first paper that prints a city and a state, leaving out the notice when its state contradicts CBP's list of ports (then a person
    confirms the place on the card)."""
    skip = "nta" if read and read.get("mismatch") else None
    return next((p for p in found if p["parts"].get("city") and p["parts"].get("state") and p["prefix"] != skip), None)


def _hold_for_reading(graph: FactGraph) -> None:
    """Fictional example or implementation helper."""
    read = reading(graph)
    if read is None:
        return
    for key in (CITY, STATE):
        fact = graph.get(key)
        if fact is None or fact.review is not None:  # a person's save is the answer; anything else waits, even a client's answer equal to the reading
            continue
        fact.value, fact.status = None, "resolved"
        fact.resolution = Resolution(chosen_value=None, reason=f"{read['paper'].capitalize()} prints \"{read['printed']}\": item 10 waits for a person "
                                                               "to confirm the place on the review card", resolved_by=RULE)


def settle(graph: FactGraph) -> None:
    """Called by assemble() after the client's own answers are recorded: puts the highest source's arrival on the facts the forms use."""
    found = papers(graph)

    # item 10, the place: the first paper that prints a city AND a state (not the notice's when its state contradicts CBP's list of ports)
    place = _placed(found, reading(graph))
    if place:
        why = f"{place['name']} outranks the client's own answer"
        _take(graph, CITY, place["keys"]["city"], why)
        _take(graph, STATE, place["keys"]["state"], why)
    else:
        _hold_for_reading(graph)

    settle_date(graph, found)

    # item 11, the manner: a DHS paper's, when there is no I-94 (an admission has an I-94, which the client's answer and its class explain)
    if not (_value(graph, "applicant.i94_number") or _value(graph, "applicant.i94_arrival_date") or _value(graph, "applicant.i94_class_of_admission")):
        said = next((p for p in found if ITEM_11.get(p["parts"].get("manner") or "")), None)
        if said:
            _take(graph, MANNER, said["keys"]["manner"], f"{said['name']} outranks the client's own answer", ITEM_11[said["parts"]["manner"]])


# --- the card -----------------------------------------------------------------------------------------------------------------


def findings(graph: FactGraph) -> list[tuple[str, str]]:
    """[(applicant.last_arrival_city, the card's text)] when the client's own answer differs from what a government paper says, or when
    two papers differ (the I-94 and the notice on the date, the notice and an I-213 on the place); nothing once a person has saved the
    card. The text says what each said, which one the I-485 uses and why, and what to do: plain words, dates MM/DD/YYYY."""
    if any(_decided(graph, k) for k in KEYS):
        return []
    found = papers(graph)
    if not found:
        return []
    lines: list[str] = []
    said_city, said_state, said_date = _typed(graph, CITY), _typed(graph, STATE), _typed(graph, SELF_DATE)
    said_manner = _typed(graph, MANNER)
    first = found[0]

    # a notice that lists more than one arrival: none is taken (not by order, not by the latest date); both texts are shown and a person chooses
    several = next((p for p in found if str(p["parts"].get("count") or "").isdigit() and int(p["parts"]["count"]) > 1), None)
    if several:
        texts = " and ".join(f"\"{t}\"" for t in str(several["parts"].get("text") or "").split(" | "))
        lines.append(f"{'The notice' if several['prefix'] == 'nta' else several['name'].capitalize()} lists more than one arrival; a person chooses: {texts}. "
                     "Nothing is filled from it.")
        found = [p for p in found if p is not several]
        first = found[0] if found else None

    # the place
    place = _placed(found, reading(graph))
    if place:
        printed = f"{place['parts']['city']}, {place['parts']['state']}"
        city_differs = bool(said_city) and _norm(said_city[0]) != _norm(place["parts"]["city"])
        state_differs = bool(said_state) and _norm(said_state[0]) != _norm(place["parts"]["state"])
        if city_differs or state_differs:
            wrote = ", ".join(x for x in (said_city[0] if said_city else "", said_state[0] if said_state else "") if x)
            lines.append(f"Place of last arrival: {place['name']} says at or near {printed}; the client wrote {wrote}.")
        for other in found:
            if other is not place and other["parts"].get("city") and (other["parts"]["city"], other["parts"].get("state")) != (place["parts"]["city"], place["parts"]["state"]):
                lines.append(f"Place of last arrival: {other['name']} says {other['parts']['city']}, {other['parts'].get('state')}, not {printed}.")
    elif first and (read := reading(graph)) and read.get("mismatch"):  # a real state, but the port is in another one: both readings
        lines.append(f"The notice prints \"{read['printed']}\". {read['words']} is a port of entry only in {read['state_words']}, not in "
                     f"{read['printed_state_words']}: the state may have been misread. Confirm or correct. Part 1, Item 10's city and state stay "
                     f"empty until a person saves this card; type the state the notice means (\"{read['printed_token']}\" as printed, or "
                     f"{read['state']} from CBP's list of ports of entry).")
        if said_city or said_state:
            wrote = ", ".join(x for x in (said_city[0] if said_city else "", said_state[0] if said_state else "") if x)
            lines.append(f"The client wrote {wrote}.")
    elif first and (read := reading(graph)):  # the city is a port in one state: the product's reading, for a person to confirm (brief K6)
        lines.append(f"The notice prints \"{read['printed']}\". {read['words']} is a port of entry in {read['state_words']}. Confirm or correct. "
                     f"The boxes below hold the product's reading ({read['city']}, {read['state']}, from CBP's list of ports of entry); Part 1, "
                     "Item 10's city and state stay empty until a person saves this card.")
        if said_city or said_state:
            wrote = ", ".join(x for x in (said_city[0] if said_city else "", said_state[0] if said_state else "") if x)
            lines.append(f"The client wrote {wrote}.")
    elif first:
        text = first["parts"].get("text") or first["parts"].get("place")
        if text and (said_city or said_state):
            wrote = ", ".join(x for x in (said_city[0] if said_city else "", said_state[0] if said_state else "") if x)
            lines.append(f"Place of last arrival: {first['name']} words it as \"{text}\", which is not a city and a state, so nothing is filled from it. "
                         f"The client wrote {wrote}.")

    # the date
    chosen = _value(graph, DATE)
    i94 = _value(graph, "applicant.i94_arrival_date")
    dated = next((p for p in found if p["parts"].get("date")), None)
    if dated and chosen:
        about = f" ({dated['parts'].get('qualifier')})" if dated["parts"].get("qualifier") else ""
        for wrote in said_date:
            if wrote != dated["parts"]["date"] and not i94:
                gap = _days(wrote, dated["parts"]["date"])
                lines.append(f"Date of last arrival: {dated['name']} says {_us(dated['parts']['date'])}{about}; the client wrote {_us(wrote)}"
                             + (f" ({gap} day{'s' if gap != 1 else ''} apart)." if gap else "."))
        if i94 and dated["parts"]["date"] != i94:
            lines.append(f"Date of last arrival: the I-94 says {_us(i94)} and {dated['name']} says {_us(dated['parts']['date'])}{about}. The I-94's own date is used.")

    # the manner
    said = next((p for p in found if ITEM_11.get(p["parts"].get("manner") or "")), None)
    if said and not i94:
        mine = ITEM_11[said["parts"]["manner"]]
        for wrote in said_manner:
            if wrote != mine:
                lines.append(f"How the client last arrived: {said['name']} says {said['parts']['manner']}; the client said {MANNER_WORDS.get(wrote, wrote.lower())}.")

    if not lines:
        return []
    chosen_paper = place or dated or said or first
    if not place and reading(graph):
        lines.append("Open the notice at the allegation, check the place and the date, correct a box if the reading is wrong, then Save.")
    elif chosen_paper:
        lines.append(f"The I-485 uses {chosen_paper['name']}: a government paper outranks the client's own answer. The client's own answer is kept here, because the "
                     "attorney may want it in the client's declaration. Open both papers, then Save (or correct a box first).")
    else:
        lines.append("Open the notice, choose the last arrival, type it in the boxes (or leave what the client wrote), then Save.")
    return [(CITY, " ".join(lines))]


def _days(a: str, b: str) -> int:
    from datetime import date

    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except ValueError:
        return 0


def card_keys(graph: FactGraph) -> list[str]:
    """The boxes the card holds: the place, the date and, when a paper settled it, the manner."""
    keys = [CITY, STATE, DATE]
    fact = graph.get(MANNER)
    if fact is not None and any(s.doc_type in ("notice_to_appear", "i213") for s in fact.sources):
        keys.append(MANNER)
    return keys


def pages(graph: FactGraph) -> list[dict[str, Any]]:
    """The DHS papers that state the arrival, each with the page the statement is on: [{doc, page, name}] for the card to open."""
    out = []
    for p in papers(graph):
        for key in p["keys"].values():
            fact = graph.get(key) if key else None
            if fact is not None and fact.sources:
                s = fact.sources[0]
                if all(o["doc"] != s.doc_id for o in out):
                    out.append({"doc": s.doc_id, "page": s.page, "name": p["name"], "raw": s.raw_value})
                break
    return out
