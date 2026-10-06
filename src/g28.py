"""The G-28's firm choices: what the form carries for the client's mailing address and for Part 4, set by the office, confirmed on every case.

Three things are decided on purpose and never by default:

  1. The client's mailing address (Part 3, items 13.a to 13.e). The form's own note says not to give the attorney's business address "unless it
     serves as the safe mailing address on the application or petition". Each office says (Settings, the office's section) whether its own address
     goes there; off means the client's own address, as before.
  2. Part 4, items 1.a, 1.b and 1.c: where USCIS sends original notices, secure identity documents and the notice that holds the I-94. Each office
     sets which boxes are marked, as a starting point.
  3. Where another form in the packet asks for a safe mailing address (the I-485's item 18, the I-765's item 5), which address it carries.

The office's choices are a firm practice the attorney approves like a rule (PRACTICE:G28 in src/rules/approval.py: who and when, and the words as
they stood). Until it is approved the product fills the client's own address and leaves Part 4 blank, and the card says so. Approved, the choices
are only the starting point for a case: a paralegal confirms the card or changes a choice with a reason, under their own name, and a packet is not
marked ready until the card is confirmed (the gate: packet.plan). Nothing here says a choice complies with anything: the words are the form's own,
and the attorney decides.

The case's record is data/clients/<id>/g28_choices.json (the data dictionary, src/records.py, has every field):

    {"version": 1,
     "choices": {"mail" | "1a" | "1b" | "1c" | "others": {"value", "reason", "by", "role", "at"}},     what a person changed from the office's setting
     "confirmed": {"by", "role", "at", "values": {...}} | null,                                         the card as it was when a person confirmed it
     "history": [{"n", "at", "by", "role", "action", "item", "before": {"choices", "confirmed"}, "undone": null | {"by", "at"}}]}

A card is "confirmed" only while the values a person confirmed are the values it shows now: a change to a choice, or to the office's setting or its
approval, makes it unconfirmed again. Every write is one row in the event ledger (kind "decisions"); an Undo puts the record back as it was before
the last change that was not already undone.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import clock
import events
import schema_path
from holders import ATTORNEY, OFFICE, held, producer


REGISTER = schema_path.path("form_text", "g28")
FILE = "g28_choices.json"
VERSION = 1
PRACTICE_ID = "PRACTICE:G28"
PRACTICE_NAME = "The G-28's choices, office by office"
ITEMS = ("1a", "1b", "1c")
POLICY_KEYS = {"mail": "office.g28_mail", "1a": "office.g28_1a", "1b": "office.g28_1b", "1c": "office.g28_1c"}
NOT_CONFIRMED = "The G-28's choices were not confirmed for this case."
UNAPPROVED = ("The attorney has not approved the office's G-28 choices yet. Until then the form carries the client's own address and Part 4 is left blank.")
# Implementation note.
WAITING = ("Waiting for an attorney to approve the office's G-28 choices (Settings, the office's section, \"Approve this practice\"; Keeping current lists it "
           "too). Until then this card cannot be confirmed or changed, and the form carries the client's own address and a blank Part 4.")
DOC_ID, DOC_TYPE = "g28 card", "g28_card"  # the source a card's address and Part 4 facts carry
GOVERNED_KEY = "g28.mail_street"  # a G-28 form whose map holds this fact key takes its address and Part 4 from the card
MAIL_PARTS = ("street", "unit_type", "apt", "city", "state", "zip")
UNIT_TYPES = ("STE", "FLR", "APT")
WHERE = {"office": "this office's address", "physical": "the client's home address", "mailing": "the client's own mailing address"}

PRACTICE = ("For each office the firm sets four choices for the G-28 (Settings, the office's section). One: whether the client's mailing address in "
            "Part 3 (items 13.a to 13.e) is this office's address or the client's own. The form's own note beside it reads: \"{note}\" "
            "Two: whether Part 4's items 1.a, 1.b and 1.c are marked. The form's own words: 1.a: \"{a}\" 1.b: \"{b}\" 1.c: \"{c}\" "
            "Until an attorney approves these choices, the product fills the client's own address and leaves Part 4 blank. Once approved, they are only "
            "the starting point for each case: a paralegal confirms them on the case's G-28 card or changes a choice with a reason, under their own "
            "name, and a packet is not marked ready until that is done. Where the I-485 and the I-765 ask for a safe or alternate mailing address, the "
            "card shows which address each carries, with the instruction paragraph for each form as USCIS prints it, and a person may choose the office's "
            "address or the client's own for the case; the product decides nothing about which cases the paragraph covers. "
            "The attorney decides whether any choice is right for the firm and for a case.")
PRACTICE_SOURCE = ("The firm's own practice for the G-28, written for the attorney's approval. The words are Form G-28's own (edition 09/17/18, Part 3's note "
                   "and Part 4, items 1.a to 1.c) and the instruction lines are copied from uscis.gov's instructions, read 10/03/2026")


# -- the words -----------------------------------------------------------------------------------------------------------

@lru_cache(maxsize=2)
def _register(stamp: float) -> dict[str, Any]:
    return json.loads(REGISTER.read_text(encoding="utf-8"))


def register() -> dict[str, Any]:
    """The form's own words and the instruction lines the product holds (schemas/forms/g28/text.json), read again when the file changes."""
    return _register(REGISTER.stat().st_mtime)


def mail_note() -> str:
    return register()["mailing_note"]


def part4_words(item: str) -> str:
    """The form's own words for a Part 4 item, with the form's own note under 1.b."""
    p = register()["part4"][item]
    return p["words"] + (" " + p["note"] if p.get("note") else "")


def instruction(form: str) -> dict[str, Any]:
    return dict(register()["instructions"][form])


# -- the office's choices (Settings) and the attorney's approval -----------------------------------------------------------

def policy(office: dict[str, Any]) -> dict[str, bool]:
    """What the office set on the Settings page: {mail, 1a, 1b, 1c}, True for on. Nothing set is off."""
    values = office.get("values") or {}
    return {item: str(values.get(key) or "off") == "on" for item, key in POLICY_KEYS.items()}


def practice_text() -> str:
    """The practice as the attorney reads it, with every office's choices as they stand: the approval holds for exactly these words."""
    import offices

    r = register()
    head = PRACTICE.format(note=r["mailing_note"], a=r["part4"]["1a"]["words"], b=part4_words("1b"), c=r["part4"]["1c"]["words"])
    lines = []
    for o in offices.offices():
        p = policy(o)
        where = "this office's address" if p["mail"] else "the client's own address"
        name = o["name"][len("Office: "):] if o["name"].startswith("Office: ") else o["name"]  # an office not yet named is "Office: new office": no second heading in the text
        lines.append(f"{name}: the client's mailing address on the G-28 is {where}; "
                     + "; ".join(f"item {i[0]}.{i[1]} is {'marked' if p[i] else 'left blank'}" for i in ITEMS) + ".")
    return head + "\n\nThe offices' choices as they stand now:\n" + "\n".join(lines)


def practice_entry() -> dict[str, Any]:
    """The practice in the approval catalog (src/rules/approval.py)."""
    text = practice_text()
    # the approval holds for the words (the form's note and Part 4 are in the text), the switches (the text lists each office's) and the instruction paragraphs
    # the card shows (hashed here): a changed paragraph shows "changed since approval" until an attorney approves again
    digest = hashlib.sha256(json.dumps({"plain_text": text, "source": PRACTICE_SOURCE, "instructions": register()["instructions"]},
                                       sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"id": PRACTICE_ID, "kind": "practice", "code": PRACTICE_ID.split(":", 1)[1], "name": PRACTICE_NAME, "plain_text": text,
            "source": PRACTICE_SOURCE, "hash": digest}


def stamp() -> tuple:
    """What the practice's words depend on: the firm's settings (every office's choices)."""
    import settings

    return (str(settings.PATH), settings.mtime(), REGISTER.stat().st_mtime)


def practice() -> dict[str, Any]:
    """The attorney's approval of the practice: {id, name, state, text, plain_text}."""
    from review.state import rule_info

    info = rule_info(PRACTICE_ID)
    return {"id": PRACTICE_ID, "name": PRACTICE_NAME, "state": info["approval"]["state"], "text": info["approval_text"], "plain_text": info["plain_text"]}


def approved() -> bool:
    return practice()["state"] == "approved"


def effective(office: dict[str, Any]) -> dict[str, Any]:
    """The office's choices as the product uses them: as set once an attorney has approved them, else the client's own address and a blank Part 4."""
    ok = approved()
    raw = policy(office)
    return {"approved": ok, "set": raw, **{k: bool(v and ok) for k, v in raw.items()}}


# -- the case's record -------------------------------------------------------------------------------------------------------

def _read(client_dir: Path) -> dict[str, Any]:
    path = Path(client_dir) / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"version": VERSION, "choices": dict(data.get("choices") or {}), "confirmed": data.get("confirmed") or None,
            "history": [h for h in data.get("history") or [] if isinstance(h, dict)]}


def _write(client_dir: Path, data: dict[str, Any]) -> None:
    path = Path(client_dir) / FILE
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _office(client_dir: Path) -> dict[str, Any]:
    import offices
    import packet

    return offices.for_case(client_dir, packet._client_state(client_dir))


def current(record: dict[str, Any], eff: dict[str, Any]) -> dict[str, Any]:
    """The choices as they stand: the office's setting, with what a person changed on the case laid over it.
    {mail: office | physical | mailing, 1a, 1b, 1c: True or False, others: office | client | None (None: as the case holds them)}"""
    ch = record.get("choices") or {}

    def pick(item: str, default: Any) -> Any:
        return (ch.get(item) or {}).get("value", default)

    out = {"mail": pick("mail", "office" if eff["mail"] else "physical"), **{i: bool(pick(i, eff[i])) for i in ITEMS}, "others": pick("others", None)}
    if not eff["approved"]:  # nothing of the office's goes on a form until an attorney approves it, whatever was recorded on the case
        out = {**out, "mail": "physical" if out["mail"] == "office" else out["mail"], **{i: False for i in ITEMS}, "others": None if out["others"] == "office" else out["others"]}
    return out


def _graph(client_dir: Path):
    """The reviewed case with no card choice laid over its mailing facts, or None when the case cannot be read (then every address is none, the same way
    at the confirmation and at every look after it)."""
    try:
        from review.state import reviewed_graph

        return reviewed_graph(client_dir, g28_card=False)
    except Exception:  # noqa: BLE001 -- a case with no readable record has no address to compare
        return None


def carried(office: dict[str, Any], graph, values: dict[str, Any]) -> dict[str, str]:
    """The address item 13 carries for these choices: the one chosen, else the client's home address, else none."""
    mine = client_addresses(graph)
    return (office_address(office) if values["mail"] == "office" else mine.get(values["mail"])) or mine["physical"] or {}


def snapshot(office: dict[str, Any], graph, values: dict[str, Any]) -> dict[str, list[str]]:
    """The address lines a confirmation covers, as the card prints them: what item 13 carries, and the office's own address (Part 1, item 3)."""
    return {"carried": lines(carried(office, graph, values)), "office": lines(office_address(office))}


def _what_moved(was: dict[str, list[str]], now: dict[str, list[str]]) -> str:
    said = []
    if was.get("carried") != now["carried"]:
        said.append("The address the form carries in item 13 is not settled now." if not now["carried"] else "The address the form carries in item 13 changed.")
    if was.get("office") != now["office"]:
        said.append("The office's address changed.")
    return " ".join(said)


def state(client_dir: Path, eff: dict[str, Any] | None = None, graph=None) -> dict[str, Any]:
    """{state: confirmed | changed | not_confirmed, values, confirmed, why}: confirmed only while what a person confirmed is what the card shows now,
    the choices and the address lines they covered; why says what moved when it is no longer."""
    record = _read(client_dir)
    office = _office(client_dir)
    eff = eff or effective(office)
    values, done = current(record, eff), record["confirmed"]
    if not done:
        return {"state": "not_confirmed", "values": values, "confirmed": None, "record": record, "why": ""}
    if done.get("values") != values:
        return {"state": "changed", "values": values, "confirmed": done, "record": record, "why": "A choice, or the office's setting or its approval, changed."}
    if done.get("address") is not None:
        why = _what_moved(done["address"], snapshot(office, graph if graph is not None else _graph(client_dir), values))
        if why:
            return {"state": "changed", "values": values, "confirmed": done, "record": record, "why": why}
    return {"state": "confirmed", "values": values, "confirmed": done, "record": record, "why": ""}


def governed(form_ids: list[str]) -> list[str]:
    """The forms among these whose address and Part 4 come from the card: a G-28 for the client as the applicant, filled with the packet."""
    ids = _governed_ids((schema_path.path("packet", "companion_forms")).stat().st_mtime)
    return [f for f in form_ids if f in ids]


@lru_cache(maxsize=2)
def _governed_ids(_stamp: float) -> frozenset[str]:
    from fill.companion import load_profile

    return frozenset(fid for fid, form in load_profile()["forms"].items() if GOVERNED_KEY in (form.get("map") or {}))


@producer(OFFICE)
def problems(client_dir: Path, form_ids: list[str]) -> list[str]:
    """The gate (src/packet.py plan): a packet with a G-28 the card governs is not ready until a person confirmed the card, and while its forms would
    send the client's mail to two different places (envelope)."""
    if not governed(form_ids):
        return []
    return ([] if state(client_dir)["state"] == "confirmed" else [NOT_CONFIRMED]) + envelope(client_dir, form_ids)


TU_CATEGORIES = ("T nonimmigrant", "U nonimmigrant")  # applicant.filing_category values whose safe mailing address is the attorney's question (docs/attorney_review.md)


def envelope(client_dir: Path, form_ids: list[str], graph=None) -> list[str]:
    """Fictional example or implementation helper."""
    others = other_forms(form_ids)
    if not governed(form_ids) or not others:
        return []
    graph = graph if graph is not None else _graph(client_dir)
    if graph is None:
        return []
    office = _office(client_dir)
    eff = effective(office)
    values = current(_read(client_dir), eff)
    g28_who = "office" if values["mail"] == "office" else "client"
    care = carries(graph)
    other_who = values["others"] or care["who"]
    if g28_who == other_who:
        return []
    g28_lines = "; ".join(lines(carried(office, graph, values))) or "no address on file"
    other_lines = "; ".join(lines(office_address(office))) if values["others"] == "office" else ("; ".join(care["lines"]) or "no address on file")
    names = " and the ".join({"i485": "I-485", "i765": "I-765"}[key] for _, key in others)
    said = (f"The G-28 sends the client's mail to {'the office' if g28_who == 'office' else 'the client'} ({g28_lines}), while the {names} send it to "
            f"{'the office, in care of the firm' if other_who == 'office' else 'the client'} ({other_lines}): one envelope, two mailing addresses.")
    category = str(_v(graph, "applicant.filing_category") or "")
    if any(category.startswith(c) for c in TU_CATEGORIES):
        return [held(ATTORNEY, said + " For a T or U case the attorney decides which address each form carries (the safe mailing address is the attorney's question), "
                                 "then records it on the case's G-28 card.")]
    if not eff["approved"]:
        return [held(ATTORNEY, said + " Approve the office's G-28 choices on Settings, then choose one address on the case's G-28 card.")]
    fix = ("choose \"The client's own address\" for the other forms on the case's G-28 card, or have the G-28 carry the office's address"
           if other_who == "office" else "choose \"The office's address, in care of the firm\" for the other forms on the case's G-28 card, or have the G-28 carry the client's address")
    return [said + " To settle it, " + fix + "."]


# -- the addresses -----------------------------------------------------------------------------------------------------------

def _v(graph, key: str) -> str:
    fact = graph.get(key) if graph is not None else None
    return str(fact.value).strip() if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else ""


def _unit(unit_type: str, apt: str) -> tuple[str, str]:
    """The suite, floor or apartment line: blank unless the address has one. The number is kept as the case holds it, with or without a type (a reviewer's
    "3B" and no APT still reaches 13.b, as it always did); a type with no number is not written."""
    unit_type, apt = unit_type.strip().upper(), apt.strip()
    return ((unit_type if unit_type in UNIT_TYPES else ""), apt) if apt else ("", "")


def office_address(office: dict[str, Any]) -> dict[str, str] | None:
    v = office.get("values") or {}
    street, city, st, zip_ = (str(v.get(k) or "").strip() for k in ("firm.street", "firm.city", "firm.state", "firm.zip"))
    if not (street and city and st and zip_):
        return None
    unit_type, apt = _unit(str(v.get("firm.unit_type") or ""), str(v.get("firm.apt") or ""))
    return {"street": street.upper(), "unit_type": unit_type, "apt": apt, "city": city.upper(), "state": st.upper(), "zip": zip_}


def client_addresses(graph) -> dict[str, dict[str, str] | None]:
    """The client's own addresses on file: the home (physical) address, and a separate mailing address when the client gave one (the firm's own
    address in care of the office is not the client's)."""
    def of(prefix: str) -> dict[str, str] | None:
        street, city, st, zip_ = (_v(graph, f"applicant.{prefix}_{p}") for p in ("street", "city", "state", "zip"))
        if not (street and city and st and zip_):
            return None
        unit_type, apt = _unit(_v(graph, f"applicant.{prefix}_unit_type"), _v(graph, f"applicant.{prefix}_apt"))
        return {"street": street.upper(), "unit_type": unit_type, "apt": apt, "city": city.upper(), "state": st.upper(), "zip": zip_}

    physical = of("physical")
    mailing = of("mailing")
    fact = graph.get("applicant.mailing_street") if graph is not None else None
    own = bool(fact is not None and (fact.review is not None or any(s.doc_type != "firm_profile" for s in fact.sources)))
    if _v(graph, "applicant.mailing_same_as_physical") == "Yes" or not own or (mailing and mailing == physical):
        mailing = None
    return {"physical": physical, "mailing": mailing}


def lines(addr: dict[str, str] | None) -> list[str]:
    """An address as the card prints it: two lines."""
    if not addr:
        return []
    unit = ", " + " ".join(x for x in (addr.get("unit_type"), addr["apt"]) if x) if addr.get("apt") else ""
    return [addr["street"] + unit, f"{addr['city']}, {addr['state']} {addr['zip']}"]


def options(office: dict[str, Any], graph) -> list[dict[str, Any]]:
    """The addresses the G-28's item 13 could carry on this case: each with its lines, or why it is not on file."""
    mine = client_addresses(graph)
    given = {"office": office_address(office), **mine}
    missing = {"office": "The office's street, city, state and ZIP are not all saved on the Settings page.",
               "physical": "The case holds no complete home address.", "mailing": "The client gave no mailing address of their own."}
    return [{"id": k, "label": WHERE[k][:1].upper() + WHERE[k][1:], "lines": lines(given[k]), "available": bool(given[k]), "why_not": "" if given[k] else missing[k]}
            for k in ("office", "physical", "mailing")]


def facts(client_dir: Path, graph) -> dict[str, str | None]:
    """What the G-28 carries for items 13.a to 13.e and Part 4, from the card as it stands (confirmed or not: an unconfirmed packet is a DRAFT)."""
    office = _office(client_dir)
    st = state(client_dir, effective(office), graph)
    values = st["values"]
    addr = carried(office, graph, values)
    out: dict[str, str | None] = {f"g28.mail_{p}": (addr.get(p) or None) for p in MAIL_PARTS}
    out.update({f"g28.part4_{i}": "Yes" if values[i] else None for i in ITEMS})
    return out


def built_with(client_dir: Path, form_ids: list[str], graph=None) -> dict[str, Any] | None:
    """What a packet was built with, for its manifest: the G-28's address and Part 4 as filled, and whether the card was confirmed. None for a packet with
    no G-28 the card governs."""
    if not governed(form_ids):
        return None
    graph = graph if graph is not None else _graph(client_dir)
    return {"facts": facts(client_dir, graph), "confirmed": state(client_dir, graph=graph)["state"] == "confirmed"}


def stale_since(client_dir: Path, form_ids: list[str], built: dict[str, Any] | None) -> str | None:
    """The sentence a packet built before a change carries: the G-28 choices changed after it was built (a packet built before this was kept says nothing)."""
    if not built or not isinstance(built.get("g28"), dict) or not governed(form_ids):
        return None
    if built_with(client_dir, form_ids) == built["g28"]:
        return None
    from review.state import us_date

    return f"The G-28 choices changed after this packet was built on {us_date(built.get('built_at'))}; rebuild it."


def stale_notes(client_dir: Path) -> list[str]:
    """The same sentence for every packet built on this case (the case page): one per packet whose manifest says it was built with the card."""
    out = []
    for path in sorted(Path(client_dir).glob("packet*.json")):
        try:
            built = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(built, dict) and isinstance(built.get("g28"), dict):
            say = stale_since(client_dir, [f.get("id") for f in built.get("forms") or []], built)
            if say:
                out.append(say)
    return out


def apply(graph, client_dir: Path):
    """Adds the card's address and Part 4 boxes to the graph as one source of their own (src/packet.py _fill_companions), for the G-28s the card governs."""
    for key, value in facts(client_dir, graph).items():
        if value and graph.get(key) is None:
            graph.add_source(key, DOC_ID, DOC_TYPE, value, value, 1.0)
    return graph


def fallback(graph):
    """For a G-28 filled without a card (the accuracy tool, a filing that renders its own forms): the client's own address, as before, and a blank Part 4."""
    if graph.get("g28.mail_street") is not None:  # the card's address, whole: its blank suite line is the office's, never the client's apartment
        return graph
    for part in MAIL_PARTS:
        value = _v(graph, f"applicant.physical_{part}")
        if value and graph.get(f"g28.mail_{part}") is None:
            graph.add_source(f"g28.mail_{part}", "fact_graph", "derived", value, value, 1.0)
    return graph


# -- the other forms' safe mailing address -----------------------------------------------------------------------------------

I765_IDS = ("i765", "ead", "i765_daca", "i765_tps")  # the I-765 under each id a packet carries it by (src/packet.py, schemas/packets/companion_forms.json)


def other_forms(form_ids: list[str]) -> list[tuple[str, str]]:
    """[(form id, the instruction paragraph's key)] for the forms in this packet that ask for a safe or alternate mailing address: the I-485, and the
    I-765 under whichever id the packet carries it by. One helper, so the card, a change and the overlay agree on which forms are meant."""
    out = [("i485", "i485")] if "i485" in form_ids else []
    return out + [(f, "i765") for f in I765_IDS if f in form_ids][:1]


def carries(graph) -> dict[str, Any]:
    """The mailing address the I-485 and the I-765 will carry now, as the case holds it: its lines and whose it is."""
    same = _v(graph, "applicant.mailing_same_as_physical") == "Yes"
    mine = client_addresses(graph)
    if same or not _v(graph, "applicant.mailing_street"):
        return {"who": "client", "lines": lines(mine["physical"]), "care_of": ""}
    care = graph.get("applicant.mailing_street")
    firm_only = care is not None and care.review is None and all(s.doc_type == "firm_profile" for s in care.sources)
    addr = {"street": _v(graph, "applicant.mailing_street"), "unit_type": "", "apt": "", "city": _v(graph, "applicant.mailing_city"),
            "state": _v(graph, "applicant.mailing_state"), "zip": _v(graph, "applicant.mailing_zip")}
    return {"who": "office" if firm_only or _v(graph, "applicant.mailing_in_care_of") else "client", "lines": lines(addr),
            "care_of": _v(graph, "applicant.mailing_in_care_of")}


def apply_mailing(graph, client_dir: Path):
    """The card's choice for the I-485's and the I-765's mailing address, laid over the reviewed case (review/state.py _office), only when a person made
    one: "office" is the office's address in care of the firm, "client" the client's own address. With no choice recorded nothing is touched, and the
    firm's in-care-of address, where the case carries it, stays."""
    choice = ((_read(client_dir)["choices"].get("others") or {}).get("value"))
    if choice not in ("office", "client"):
        return graph
    who = str((_read(client_dir)["choices"]["others"]).get("by") or "the G-28 card")
    import offices

    if choice == "office":
        if not approved():  # nothing of the office's goes on a form until an attorney approves it
            return graph
        office = offices.for_case(client_dir, offices._state_of(graph))
        v = office["values"]
        if not all(v.get(k) for k in offices.MAILING):
            return graph
        for firm_key, key in offices.MAILING.items():
            graph.set_by_review(key, str(v[firm_key]), who, "chosen on the G-28 card: the office's address")
        graph.set_by_review("applicant.mailing_same_as_physical", "No", who, "chosen on the G-28 card: the office's address")
        return graph
    graph.set_aside({offices.FIRM_DOC, "companion_forms.json"}, "applicant.mailing_")
    if graph.get("applicant.mailing_street") is None and graph.get("applicant.mailing_same_as_physical") is None:
        graph.add_source("applicant.mailing_same_as_physical", DOC_ID, DOC_TYPE, "Yes", "Yes", 1.0)
    return graph


# -- the card ------------------------------------------------------------------------------------------------------------

def card(client_dir: Path, form_ids: list[str], graph) -> dict[str, Any] | None:
    """The review card "The G-28 for this case" (None when the packet holds no G-28 the card governs). graph: the reviewed case, with no card choice
    laid over its mailing facts (review/state.py reviewed_graph(g28_card=False))."""
    forms = governed(form_ids)
    if not forms:
        return None
    office = _office(client_dir)
    eff = effective(office)
    st = state(client_dir, eff, graph)
    record, values = st["record"], st["values"]
    ch = record["choices"]
    r = register()
    v = office["values"]
    first = lambda *keys: next((str(v.get(k)).strip() for k in keys if v.get(k)), "")  # noqa: E731
    attorney = " ".join(x for x in (first("firm.preparer_given_name"), first("firm.preparer_family_name")) if x)

    def changed(item: str) -> dict[str, Any] | None:
        c = ch.get(item)
        return {"reason": c.get("reason") or "", "by": c.get("by") or "", "role": c.get("role") or "", "at": c.get("at")} if c else None

    opts = options(office, graph)
    default_mail = "office" if eff["mail"] else "physical"
    part4 = [{"id": i, "item": r["part4"][i]["item"], "short": r["part4"][i]["short"], "words": r["part4"][i]["words"], "note": r["part4"][i].get("note") or "",
              "office_setting": eff[i], "marked": values[i], "changed": changed(i)} for i in ITEMS]
    others = []
    for fid, key in other_forms(form_ids):
        ins = instruction(key)
        others.append({"id": fid, "title": ins["title"], "document": ins["document"], "url": ins["url"], "read": ins["read"], "line": ins["line"]})
    care = carries(graph) if others else None
    done = st["confirmed"]
    return {
        "title": "The G-28 for this case", "forms": forms,
        "state": st["state"], "why": st["why"], "confirmed": {k: done.get(k) for k in ("by", "role", "at")} if done else None,
        "attorney": {"name": attorney, "bar": first("firm.attorney_bar_number"), "office": office.get("name") or "", "address": lines(office_address(office))},
        "no_office": _no_office(office),
        "client": {"name": " ".join(x for x in (_v(graph, "applicant.given_name"), _v(graph, "applicant.middle_name"), _v(graph, "applicant.family_name")) if x)},
        "policy": {"approved": eff["approved"], "set": eff["set"], "approval": practice(),
                   "says": "" if eff["approved"] else WAITING, "waiting": not eff["approved"]},
        "mail": {"item": r["mailing_item"], "note": r["mailing_note"], "instruction": instruction("g28"), "options": opts, "office_setting": default_mail,
                 "chosen": values["mail"], "changed": changed("mail")},
        "part4": part4, "part4_heading": r["part4_heading"], "part4_intro": r["part4_intro"], "part4_instruction": instruction("g28_part4"),
        "others": {"forms": others, "carries": care, "chosen": values["others"], "changed": changed("others"),
                   "decides": "The attorney decides which cases this covers."},
        "decides": "The attorney decides whether these choices are right for this case. The words above are the form's own and the instructions' own.",
        "history": [{"n": h.get("n"), "at": h.get("at"), "by": h.get("by"), "role": h.get("role"), "what": _what(h), "undone": h.get("undone")}
                    for h in record["history"][-12:]],
    }


def _no_office(office: dict[str, Any]) -> str:
    """Fictional example or implementation helper."""
    import offices
    import settings

    return offices.NO_OFFICE if office.get("id") == offices.MAIN and settings.identity_withheld() else ""


ITEM_WORDS = {"mail": "the client's mailing address on the G-28", "1a": "Part 4, item 1.a", "1b": "Part 4, item 1.b", "1c": "Part 4, item 1.c",
              "others": "the mailing address on the I-485 and the I-765"}


# what a ledger row says a change was to, each naming the G-28 once
LEDGER_WORDS = {"mail": "the client's mailing address on the G-28", "1a": "Part 4, item 1.a on the G-28", "1b": "Part 4, item 1.b on the G-28",
                "1c": "Part 4, item 1.c on the G-28", "others": "the mailing address on the I-485 and the I-765, from the G-28's card"}


def _what(h: dict[str, Any]) -> str:
    item = ITEM_WORDS.get(h.get("item") or "", "")
    return {"confirmed": "Confirmed the G-28's choices", "changed": f"Changed {item}", "reset": f"Went back to the office's setting for {item}",
            "undone": "Took back the last change"}.get(h.get("action") or "", "Changed the card")


# -- the writes ----------------------------------------------------------------------------------------------------------

def _who(by: str) -> str:
    by = str(by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every choice on the G-28 records who made it.")
    return by


def _push(record: dict[str, Any], action: str, item: str | None, who: str, role: str | None) -> None:
    before = {"choices": json.loads(json.dumps(record["choices"])), "confirmed": json.loads(json.dumps(record["confirmed"]))}
    n = max([h.get("n") or 0 for h in record["history"]] + [0]) + 1
    record["history"].append({"n": n, "at": clock.stamp(), "by": who, "role": role, "action": action, "item": item, "before": before, "undone": None})


def _refuse(client_dir: Path, what: str, who: str, role: str | None) -> None:
    """A confirmation or a change asked for while the office's choices wait for the attorney: refused with WAITING, and the ledger says so."""
    events.record("decisions", "refused", f"Refused: {what} (the office's G-28 choices are not approved yet)", case_dir=client_dir, who=who, role=role)
    raise PermissionError(WAITING)


def confirm(client_dir: Path, by: str, role: str | None = None, graph=None) -> dict[str, Any]:
    """Fictional example or implementation helper."""
    who = _who(by)
    office = _office(client_dir)
    eff = effective(office)
    if not eff["approved"]:
        _refuse(client_dir, "confirming the G-28's choices for the case", who, role)
    record = _read(client_dir)
    values = current(record, eff)
    available = {o["id"]: o["available"] for o in options(office, graph)} if graph is not None else {}
    if available and not available.get(values["mail"], True):
        raise ValueError("The address chosen for the G-28 is not on file: choose another, or save it first.")
    graph = graph if graph is not None else _graph(client_dir)
    _push(record, "confirmed", None, who, role)
    record["confirmed"] = {"by": who, "role": role, "at": clock.stamp(), "values": values, "address": snapshot(office, graph, values)}
    _write(client_dir, record)
    events.record("decisions", "confirmed", "Confirmed the G-28's choices for the case", case_dir=client_dir, who=who, role=role)
    return state(client_dir)


def change(client_dir: Path, item: str, value: Any, reason: str, by: str, role: str | None = None, graph=None) -> dict[str, Any]:
    """A person changes one choice for this case, with a reason; choosing what the office's setting says is going back to it, with no reason needed."""
    who = _who(by)
    if item not in ITEM_WORDS:
        raise ValueError("Choose which of the card's choices to change.")
    eff = effective(_office(client_dir))
    if not eff["approved"]:  # Implementation note.
        _refuse(client_dir, f"a change to {LEDGER_WORDS[item]}", who, role)
    record = _read(client_dir)
    if item == "mail":
        if value not in WHERE:
            raise ValueError("Choose the office's address, the client's home address or the client's own mailing address.")
        office = _office(client_dir)
        if graph is not None and not next((o["available"] for o in options(office, graph) if o["id"] == value), False):
            raise ValueError("That address is not on file for this case.")
        default: Any = "office" if eff["mail"] else "physical"
    elif item == "others":
        if value not in ("office", "client", None, ""):
            raise ValueError("Choose the office's address or the client's own address.")
        value = value or None
        default = None
    else:
        value = value in (True, "true", "on", "yes", "Yes", 1)
        default = eff[item]
    reason = " ".join(str(reason or "").split())
    if value == default:
        if item not in record["choices"]:
            return state(client_dir)
        _push(record, "reset", item, who, role)
        record["choices"].pop(item)
        action = "reset"
    else:
        if not reason:
            raise ValueError("Say why this case differs from the office's setting: every change on the G-28 records a reason.")
        _push(record, "changed", item, who, role)
        record["choices"][item] = {"value": value, "reason": reason[:600], "by": who, "role": role, "at": clock.stamp()}
        action = "changed"
    _write(client_dir, record)
    said = "Changed" if action == "changed" else "Went back to the office's setting for"
    events.record("decisions", action, f"{said} {LEDGER_WORDS[item]}", case_dir=client_dir, who=who, role=role)
    return state(client_dir)


def undo(client_dir: Path, by: str, role: str | None = None) -> dict[str, Any]:
    """Puts the card back as it was before the last change that was not already taken back."""
    who = _who(by)
    record = _read(client_dir)
    last = next((h for h in reversed(record["history"]) if not h.get("undone") and h.get("action") != "undone"), None)
    if last is None:
        raise ValueError("There is nothing to take back on this card.")
    before = last["before"]
    last["undone"] = {"by": who, "role": role, "at": clock.stamp()}
    record["choices"], record["confirmed"] = before.get("choices") or {}, before.get("confirmed")
    n = max(h.get("n") or 0 for h in record["history"]) + 1
    record["history"].append({"n": n, "at": clock.stamp(), "by": who, "role": role, "action": "undone", "item": last.get("item"),
                              "before": {"choices": {}, "confirmed": None}, "undone": None})
    _write(client_dir, record)
    events.record("decisions", "undone", "Took back the last change to the G-28's choices", case_dir=client_dir, who=who, role=role)
    return state(client_dir)


# -- the review bundle -------------------------------------------------------------------------------------------------------

def bundle_rows(client_dir: Path) -> dict[str, Any] | None:
    """What the review bundle says of the card: who confirmed it and when, each choice as the case has it, each change with its reason and person."""
    path = Path(client_dir) / FILE
    if not path.exists():
        return None
    st = state(client_dir)
    record, done = st["record"], st["confirmed"]
    r = register()
    values = st["values"]
    eff = effective(_office(client_dir))
    return {
        "state": st["state"], "confirmed": {k: done.get(k) for k in ("by", "role", "at")} if done else None,
        "approval": practice(), "mail_note": r["mailing_note"],
        "mail": {"chosen": values["mail"], "where": WHERE.get(values["mail"], ""), "office_setting": "office" if eff["mail"] else "physical", "changed": record["choices"].get("mail")},
        "part4": [{"item": r["part4"][i]["item"], "words": r["part4"][i]["words"], "marked": values[i], "office_setting": eff[i], "changed": record["choices"].get(i)} for i in ITEMS],
        "others": {"chosen": values["others"], "changed": record["choices"].get("others")},
        "history": [{"at": h.get("at"), "by": h.get("by"), "role": h.get("role"), "what": _what(h), "undone": h.get("undone")} for h in record["history"]],
    }
