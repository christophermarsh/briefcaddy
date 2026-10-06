"""One engine for a filing's own questions -- the green-card renewal (I-90),
travel documents (I-131), the certificate of citizenship (N-600), removing
conditions (I-751) -- so each new filing is only what is different about it:

  - SECTIONS: the questions, as the form asks them, each with who answers it;
  - derive(graph, today): the filing's facts from what the case already holds
    (each a derived source that traces to its document; a person's answer wins);
  - problems(client_dir, graph, today): what stops the packet from being final;
  - notes(graph, today): what the panel says on top (dates, where it is filed).

Answers are saved as ordinary review decisions ("<code>:<fact key>"), so they
reach the filled form like every other correction, and show who gave them.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import clock
import holders
import schema_path

YES_NO = {"type": "choice", "options": ["Yes", "No"]}
TEXT = {"type": "text"}
DATE = {"type": "date"}
LINES = {"type": "text", "multiline": True}

# filing code -> module (each defines SECTIONS, derive, problems, notes)
MODULES = {"i90": "card_renewal", "i131": "travel", "n600": "certificate", "i751": "conditions", "eoir28": "court", "visa": "visa", "ead": "work_permit", "address": "address_change", "asylee": "asylee", "i290b": "motion", "n336": "hearing_request", "bia": "bia", "i601a": "waiver", "i912": "fee_waiver", "expedite": "expedite",
           "cancellation": "cancellation"}
MODULES["caa"] = "cuban_adjustment"  # the Cuban Adjustment Act (and HRIFA dependents): an I-485 in Part 2, 3.f
MODULES["vawa"] = "vawa"  # the VAWA self-petition on Form I-360 (src/vawa.py)
MODULES["i914"] = "t_visa"  # the T visa (src/t_visa.py)
MODULES["i914b"] = "t_visa_declaration"  # its Supplement B request
MODULES["u_cert"] = "u_certification"  # the U visa certification request (src/u_certification.py)
MODULES["u_visa"] = "u_visa"  # the U visa petition (src/u_visa.py)
MODULES["daca"] = "daca"  # a DACA renewal: the I-821D, the I-765 (c)(33) and the I-765WS
MODULES["i730"] = "i730"  # the asylee's or refugee's relative petition (src/i730.py)
MODULES["court_bond"] = "bond"  # a written bond (custody redetermination) request to the immigration judge (src/bond.py)
MODULES["court_motion"] = "court_motion"  # a motion to reopen or reconsider before the immigration judge (src/court_motion.py)
MODULES["i601"] = "inadmissibility_waiver"  # the waiver of grounds of inadmissibility, and the I-212 with it (src/inadmissibility_waiver.py)
MODULES["i212"] = "reapply"  # permission to reapply after removal (src/reapply.py)
MODULES["n565"] = "n565"  # a replacement naturalization or citizenship certificate (src/n565.py)
MODULES["g639"] = "g639"  # a FOIA request for the client's USCIS file (src/g639.py)
MODULES["tps"] = "tps"  # a TPS registration or re-registration: the I-821 with the I-765 (src/tps.py)
MODULES["parole"] = "parole"  # humanitarian parole for someone outside the U.S.: the I-131 (Part 1, item 7) with an I-134 for each person (src/parole.py)


def module(filing: str):
    return importlib.import_module(MODULES[filing]) if filing in MODULES else None


def value(graph, key: str) -> Any:
    fact = graph.get(key)
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def iso(v: Any) -> date | None:
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(v or ""))
    try:
        return date.fromisoformat(m.group(0)) if m else None
    except ValueError:
        return None


def us(d: date | None) -> str:
    return d.strftime("%m/%d/%Y") if d else "?"


def plus_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return date(d.year + years, 3, 1)


def putter(graph, source: str = "filing.derive") -> Callable[[str, Any, str], None]:
    def put(key: str, v: Any, why: str) -> None:
        if v not in (None, "") and value(graph, key) is None:
            graph.add_source(key, source, "derived", why, v, 0.85, tier=3)
    return put


SCHEMAS = schema_path.ROOT
STATES = {"ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
          "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
          "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
          "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ",
          "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
          "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
          "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY", "PUERTO RICO": "PR",
          "GUAM": "GU", "VIRGIN ISLANDS": "VI", "U.S. VIRGIN ISLANDS": "VI", "AMERICAN SAMOA": "AS", "NORTHERN MARIANA ISLANDS": "MP"}


def money(n: Any) -> str:
    return f"${n:,}" if isinstance(n, (int, float)) else "[fee]"


def state_of(graph) -> str:
    """The two-letter state the client lives in (the home address; else the mailing address)."""
    raw = str(value(graph, "applicant.physical_state") or value(graph, "applicant.mailing_state") or "").strip().upper()
    return STATES.get(raw, raw)


def lockbox(chart: str, state: str) -> tuple[list[str] | None, str | None]:
    """(the USPS address lines, the lockbox's name) for a state on one of
    USCIS's charts in schemas/law (each "lockboxes": {name: {usps, states}})."""
    boxes = json.loads(schema_path.path("law", chart, SCHEMAS).read_text(encoding="utf-8"))["lockboxes"]
    return next(((list(box["usps"]), name) for name, box in boxes.items() if state in box.get("states", [])), (None, None))


def addresses(graph, put: Callable[[str, Any, str], None], prefix: str) -> None:
    """The form's mailing address (the client's, or the home address when the
    mail goes there) and its home address only when different -- most forms
    ask the second only "if different"."""
    different = value(graph, "applicant.mailing_same_as_physical") == "No"
    for part in ("street", "unit_type", "apt", "city", "state", "zip"):
        put(f"{prefix}.mailing_{part}", value(graph, f"applicant.mailing_{part}") if different else value(graph, f"applicant.physical_{part}"),
            "the client's mailing address" if different else "the client's home address (also their mailing address)")
        if different:
            put(f"{prefix}.physical_{part}", value(graph, f"applicant.physical_{part}"), "the client's home address")


def latest_notice(graph, form: str, *kinds: str) -> dict[str, Any] | None:
    """The client's latest USCIS notice for a form (of these kinds: receipt, approval...)."""
    import journey

    found = [n for n in journey.notices(graph) if n["form"] == form and (not kinds or n["kind"] in kinds)]
    return found[-1] if found else None


def pending(graph, form: str) -> dict[str, Any] | None:
    """The receipt notice of a filing USCIS hasn't decided yet (no approval, denial or rejection since)."""
    receipt = latest_notice(graph, form, "receipt")
    decided = latest_notice(graph, form, "approval", "denial", "rejection")
    if receipt and not (decided and decided["receipt"] == receipt["receipt"]):
        return receipt
    return None


def sij(graph) -> bool:
    """Seeking or granted Special Immigrant Juvenile classification (the case's own evidence of it)."""
    import journey

    if value(graph, "vawa.classification"):  # a VAWA self-petitioner's I-360 is not an SIJ petition (src/vawa.py)
        return False
    forms = {n["form"] for n in journey.notices(graph)}
    category = str(value(graph, "applicant.filing_category") or "").lower()
    return bool("I-360" in forms or value(graph, "applicant.i360_receipt_number") or value(graph, "applicant.i360_priority_date")
                or "juvenile" in category or value(graph, "applicant.public_charge_exemption") == "SIJS")


def has_doc(client_dir: Path, *doc_types: str) -> list[str]:
    meta = json.loads((client_dir / "meta.json").read_text(encoding="utf-8")) if (client_dir / "meta.json").exists() else {}
    return [doc for doc, kind in (meta.get("classifications") or {}).items() if kind in doc_types]


def in_exhibit(client_dir: Path, filing: str, exhibit: str) -> list[str]:
    """Documents the paralegal put in one of this filing's exhibits -- evidence the
    reader has no type for (a death certificate, a custody order) counts once placed."""
    path = client_dir / f"packet_choices_{filing}.json"
    files = (json.loads(path.read_text(encoding="utf-8")).get("files") or {}) if path.exists() else {}
    return [doc for doc, chosen in files.items() if chosen == exhibit]


def questions(mod) -> list[tuple[str, str, str, dict, bool, str]]:
    """(key, label, section, input, required, who) for every question."""
    return [(key, label, section, spec, required, who) for section, who, items, *_when in mod.SECTIONS for key, label, spec, required in items]


def hidden_sections(mod, graph) -> set[str]:
    """Sections that don't apply to this case yet: a section's optional fourth element says when it is shown
    (the I-131's advance parole questions, once advance parole is the document)."""
    return {section for section, _who, _items, *when in mod.SECTIONS if when and not when[0](graph)}


def graph_for(filing: str, client_dir: Path, today: date | None = None):
    from review.state import reviewed_graph

    return derive(module(filing), client_dir, reviewed_graph(client_dir), today or clock.today())


def derive(mod, client_dir: Path, graph, today: date):
    """The module's own facts: from the case's record first when the module reads it (from_record -- a move recorded on the
    case page), then from the graph."""
    if hasattr(mod, "from_record"):
        path = client_dir / "status.json"
        mod.from_record(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}, graph)
    if hasattr(mod, "from_case"):  # what the case's own folder holds beyond its facts (the EOIR-26A's figures, signature and attestation: src/eoir26a.py)
        mod.from_case(client_dir, graph)
    return mod.derive(graph, today)


def status(filing: str, client_dir: Path, today: date | None = None) -> dict[str, Any]:
    mod, today = module(filing), today or clock.today()
    graph = graph_for(filing, client_dir, today)
    shut = mod.closed(graph, today) if hasattr(mod, "closed") else None
    if shut:  # nothing can be filed (a terminated TPS designation): the sentence alone, no questions and no packet; the problem keeps a packet from being built
        return {"filing": filing, "title": mod.TITLE, "questions": [], "notes": [shut], "problems": [holders.held(holders.ATTORNEY, shut["text"])], "client_questions": [], "closed": True}
    hidden = hidden_sections(mod, graph)
    qs = []
    for key, label, section, spec, required, who in questions(mod):
        if section in hidden:
            continue
        fact = graph.get(key)
        sources = [{"doc": s.doc_id, "type": s.doc_type, "raw": s.raw_value} for s in (fact.sources if fact is not None else [])][:3]
        review = getattr(fact, "review", None) if fact is not None else None
        qs.append({"key": key, "label": label, "section": section, "input": spec, "required": required, "who": who,
                   "value": value(graph, key), "sources": sources, "answered_by": review.resolved_by if review is not None else None})
    missing = [q["label"].replace(" -- ", ": ") for q in qs if q["required"] and q["value"] is None]
    more = f"; and {len(missing) - 4} more" if len(missing) > 4 else ""
    found = [f"{mod.TITLE} questions not answered yet ({len(missing)}): {'; '.join(missing[:4])}{more}"] if missing else []
    found = [holders.held(holders.of_who(next(q["who"] for q in qs if q["required"] and q["value"] is None)), f if f.endswith(("?", ".")) else f + ".") for f in found]  # held by whoever the first of them is for
    more = [{"level": "info", "title": "More questions to come",
             "text": getattr(mod, "MORE_QUESTIONS", None) or "More questions appear here once the answers above say they apply."}] \
        if hidden and getattr(mod, "more_to_come", lambda g: True)(graph) else []  # a module can say that its hidden sections can no longer appear
    # whose documents are in the folder (the module's document_notes: src/documents.py person)
    people = mod.document_notes(client_dir, graph) if hasattr(mod, "document_notes") else []
    return {"filing": filing, "title": mod.TITLE, "questions": qs, "notes": mod.notes(graph, today) + more + people,
            "problems": found + mod.problems(client_dir, graph, today),
            "client_questions": [{"key": key, "text": words["en"]} for key, words in client_questions(mod, graph).items()
                                 if value(graph, key) is None]}


def client_questions(mod, graph) -> dict[str, dict[str, str]]:
    """The long answers a filing asks the client in their portal, in each language the portal speaks ({key: {"en", "pt", "es", "ht"}}):
    the module's CLIENT_QUESTIONS (or its client_questions(graph), for those only some cases ask). DRAFT wording."""
    if hasattr(mod, "client_questions"):
        return mod.client_questions(graph)
    return getattr(mod, "CLIENT_QUESTIONS", {})


def ask_in_portal(filing: str, client_dir: Path | None, lang: str) -> list[tuple[str, str, dict[str, Any]]]:
    """(key, the English, the typed fields portal/store.add_request keeps) for each of the filing's portal questions the case hasn't
    answered: the module's own wording in the client's language, no machine translation on top of it. A language the module has no
    wording for goes in English, marked for a translator, as the office's typed questions do (portal/questions.py)."""
    from factgraph import FactGraph

    mod = module(filing)
    graph = graph_for(filing, client_dir) if client_dir is not None and (client_dir / "fact_graph.json").exists() else FactGraph("portal")
    out = []
    for key, words in client_questions(mod, graph).items():
        if value(graph, key) is not None:
            continue
        own = words.get(lang) if lang != "en" else words["en"]
        out.append((key, words["en"], {"type": "text", "text_client": own or "", "language": lang, "machine_translated": lang not in ("en", "pt", "es") and bool(own),
                                       "needs_translator": not own, "wording": "DRAFT: the filing's own wording, for the attorney and a certified translator"}))
    return out


@holders.producer(holders.OFFICE)
def problems(filing: str, client_dir: Path, today: date | None = None) -> list[str]:
    return status(filing, client_dir, today)["problems"]


def _checked(label: str, spec: dict[str, Any], raw: Any) -> Any:
    """An answer as its question allows it: one of the choices, a real date (YYYY-MM-DD), or text."""
    if raw in ("", None):
        return None
    raw = str(raw).strip()
    if spec.get("type") == "choice" and raw not in spec["options"]:
        raise ValueError(f"\"{label}\": choose one of {', '.join(spec['options'])}.")
    if spec.get("pattern") and not re.fullmatch(spec["pattern"], raw):
        raise ValueError(f"\"{label}\": written like {spec.get('placeholder') or 'the box on the form'}.")
    if spec.get("digits") and not raw.isdigit():
        raise ValueError(f"\"{label}\": numbers only.")
    if spec.get("type") == "date" and not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) and iso(raw)):
        raise ValueError(f"\"{label}\": a date as YYYY-MM-DD.")
    return raw


def answer(filing: str, client_dir: Path, values: dict[str, Any], reviewer: str, role: str | None = None) -> dict[str, Any]:
    from review.state import record_decision

    if not reviewer:
        raise ValueError("Enter your name first: every answer records who gave it.")
    specs = {key: (label, spec) for key, label, _s, spec, _r, _w in questions(module(filing))}
    unknown = [key for key in values if key not in specs]
    if unknown:
        raise ValueError(f"{', '.join(unknown)}: not a question on this filing.")
    checked = {key: _checked(specs[key][0], specs[key][1], raw) for key, raw in values.items()}  # all valid before any is saved
    for key, raw in checked.items():
        label, spec = specs[key]
        item = {"id": f"{filing}:{key}", "kind": filing, "level": "review", "title": label, "group": "attorney", "actions": ["set", "blank"],
                "facts": [{"key": key, "label": label, "input": spec}]}
        decision = {"action": "blank" if raw is None else "set", "values": {} if raw is None else {key: raw},
                    "reviewer": reviewer, **({"role": role} if role else {}), "note": f"{filing} question"}
        record_decision(client_dir, item, decision)
    return status(filing, client_dir)
