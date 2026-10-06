"""The step before the I-485 for an SIJ client: Form I-360, the Special
Immigrant Juvenile petition (schemas/packets/i360.json for the packet,
schemas/packets/companion_forms.json "i360" for the form).

Most of the I-360 is the same reviewed case the I-485 uses (name, birth,
A-Number, address, passport, I-94). What it adds is Part 8 -- the state
court's findings -- and a few questions only the client or the attorney can
answer. The court order is read (extract/sij_order.py); every answer here
shows where it came from, and a person can set or correct it -- saved as an
ordinary review decision, so it reaches the filled form like any other fix.

The deadline that matters: the petition must be filed before the
petitioner's 21st birthday. USCIS lets a petitioner close to 21 file in
person at a field office in the two weeks before (an expedite appointment
through the USCIS Contact Center, 800-375-5283).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import clock
from holders import ATTORNEY, CLIENT, OFFICE, held, of_first, producer

YES_NO = {"type": "choice", "options": ["Yes", "No"]}
# (fact key, question as the form asks it, who knows the answer, input, required)
QUESTIONS: list[tuple[str, str, str, dict[str, Any], bool]] = [
    ("sij.declared_dependent", "Part 8, 2.A · Declared dependent on a juvenile court, or committed to / placed in the custody of an agency or a person?",
     "court order", YES_NO, True),
    ("sij.placed_with", "Part 8, 2.B · The agency, department or person the child is placed with (e.g. the guardian)", "court order", {"type": "text"}, False),
    ("sij.under_court_jurisdiction", "Part 8, 2.C · Still under the jurisdiction of the juvenile court that made the order?", "attorney", YES_NO, True),
    ("sij.residing_in_placement", "Part 8, 3.A · If yes to 2.C: living in the court-ordered placement?", "client", YES_NO, False),
    ("sij.not_under_jurisdiction_reason", "Part 8, 3.B · If no to 2.C: why (adopted or permanent guardianship / aged out / other)", "attorney",
     {"type": "choice", "options": ["adopted", "aged_out", "other"]}, False),
    ("sij.reunification_parents", "Part 8, 4.A · Reunification not viable with one or both parents", "court order", {"type": "choice", "options": ["one", "both"]}, True),
    ("sij.ground_abuse", "Part 8, 4.A · ground: abuse", "court order", {"type": "choice", "options": ["Yes"]}, False),
    ("sij.ground_neglect", "Part 8, 4.A · ground: neglect", "court order", {"type": "choice", "options": ["Yes"]}, False),
    ("sij.ground_abandonment", "Part 8, 4.A · ground: abandonment", "court order", {"type": "choice", "options": ["Yes"]}, False),
    ("sij.ground_similar", "Part 8, 4.A · ground: a similar basis under state law", "court order", {"type": "choice", "options": ["Yes"]}, False),
    ("sij.similar_basis", "Part 8, 4.A · the similar basis, as the order states it", "court order", {"type": "text"}, False),
    ("sij.parent_name", "Part 8, 4.B · If one parent: that parent's name", "court order", {"type": "text"}, False),
    ("sij.best_interest_determined", "Part 8, 5 · Determined that returning to the home country is not in the child's best interest?", "court order", YES_NO, True),
    ("sij.hhs_custody", "Part 8, 6.A · Ever in the custody of HHS (an ORR shelter or sponsor placement)?", "client", YES_NO, True),
    ("sij.hhs_order_altered", "Part 8, 6.B · If in HHS custody now: did the court order determine or change custody or placement?", "attorney", YES_NO, False),
    ("applicant.part9.in_removal_proceedings", "Part 4, 5 · In removal proceedings (immigration court)?", "client / Notice to Appear", YES_NO, True),
    ("applicant.part9.worked_without_authorization", "Part 4, 6 · Ever worked in the U.S. without permission?", "client", YES_NO, True),
]
GROUNDS = ("sij.ground_abuse", "sij.ground_neglect", "sij.ground_abandonment", "sij.ground_similar")
URGENT_DAYS = 60


def _graph(client_dir: Path):
    from review.state import reviewed_graph

    return reviewed_graph(client_dir)


def _value(graph, key: str) -> Any:
    fact = graph.get(key)
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def turns_21(dob_iso: str | None) -> date | None:
    if not dob_iso:
        return None
    try:
        d = date.fromisoformat(str(dob_iso))
    except ValueError:
        return None
    try:
        return d.replace(year=d.year + 21)
    except ValueError:  # born on February 29
        return date(d.year + 21, 3, 1)


def age_check(dob_iso: str | None, today: date | None = None) -> dict[str, Any]:
    """The 21st-birthday deadline: {"turns_21", "days_left", "level", "text"}."""
    today = today or clock.today()
    deadline = turns_21(dob_iso)
    if deadline is None:
        return {"turns_21": None, "days_left": None, "level": "check", "text": "No date of birth in the case: the I-360 must be filed before the 21st birthday."}
    days = (deadline - today).days
    us = deadline.strftime("%m/%d/%Y")
    if days <= 0:
        return {"turns_21": deadline.isoformat(), "days_left": days, "level": "blocking",
                "text": f"Turned 21 on {us}: the I-360 can no longer be filed as a Special Immigrant Juvenile. Ask the attorney."}
    if days <= URGENT_DAYS:
        return {"turns_21": deadline.isoformat(), "days_left": days, "level": "urgent",
                "text": f"Turns 21 on {us}: {days} day{'s' if days != 1 else ''} left. File now; in the last two weeks USCIS allows in-person "
                        "filing at a field office (expedite appointment through the USCIS Contact Center, 800-375-5283)."}
    return {"turns_21": deadline.isoformat(), "days_left": days, "level": "ok", "text": f"Turns 21 on {us} ({days} days)."}


def _has_doc(client_dir: Path, doc_type: str) -> list[str]:
    meta = json.loads((client_dir / "meta.json").read_text(encoding="utf-8")) if (client_dir / "meta.json").exists() else {}
    return [doc for doc, kind in (meta.get("classifications") or {}).items() if kind == doc_type]


def status(client_dir: Path, today: date | None = None) -> dict[str, Any]:
    """Where this client stands for the I-360, and its questions with their answers and sources."""
    graph = _graph(client_dir)
    approved = bool(_has_doc(client_dir, "i360_approval") or _value(graph, "applicant.i360_receipt_number"))
    questions = []
    for key, label, who, spec, required in QUESTIONS:
        fact = graph.get(key)
        value = _value(graph, key)
        sources = [{"doc": s.doc_id, "type": s.doc_type, "raw": s.raw_value} for s in (fact.sources if fact is not None else [])][:3]
        questions.append({"key": key, "label": label, "who": who, "input": spec, "required": required, "value": value, "sources": sources, "answered_by": getattr(getattr(fact, "review", None), "resolved_by", None)})
    return {"approved": approved, "court_orders": _has_doc(client_dir, "sij_order"), "age": age_check(_value(graph, "applicant.dob"), today),
            "questions": questions, "problems": problems(client_dir, today, graph=graph, approved=approved, questions=questions)}


@producer(OFFICE)
def problems(client_dir: Path, today: date | None = None, graph=None, approved: bool | None = None, questions: list | None = None) -> list[str]:
    """What stops the I-360 packet from being final (src/packet.py adds these)."""
    if questions is None:
        return status(client_dir, today)["problems"]
    out = []
    if approved:
        out.append(held(ATTORNEY, "This client already has an approved I-360 (Form I-797 in the folder): file the I-485 packet instead."))
    age = age_check(_value(graph, "applicant.dob"), today)
    if age["level"] in ("blocking", "urgent", "check"):
        out.append(held(CLIENT if age["turns_21"] is None else ATTORNEY if age["level"] == "blocking" else OFFICE, age["text"]))
    unanswered = [re.split(r" · | -- |: ", q["label"], maxsplit=1)[0] for q in questions if q["required"] and q["value"] is None]
    if unanswered:
        out.append(held(of_first(questions), f"I-360 questions not answered yet: {', '.join(unanswered)}."))
    if not any(q["value"] for q in questions if q["key"] in GROUNDS):
        if _value(graph, "sij.order_form") == "CJP 37":
            out.append("Part 8, 4.A-4.B: the order is the Massachusetts judgment (CJP 37). Its tick boxes. The grounds for each parent, "
                       "mother or father, and whether Parent Two applies. Can't be read from a scan: read them on the order (items 2, 6 "
                       "and 7) and answer here. A filled PDF of the judgment is read exactly.")
        else:
            out.append("Part 8, 4.A: no ground selected (abuse, neglect, abandonment or a similar basis). Read the court order.")
    by_key = {q["key"]: q["value"] for q in questions}
    if by_key.get("sij.reunification_parents") == "one" and not by_key.get("sij.parent_name"):
        out.append("Part 8, 4.B: reunification is not viable with one parent. Enter that parent's name.")
    if by_key.get("sij.under_court_jurisdiction") == "No" and not by_key.get("sij.not_under_jurisdiction_reason"):
        out.append("Part 8, 3.B: not under the court's jurisdiction. Choose why.")
    return out


def answer(client_dir: Path, values: dict[str, Any], reviewer: str, role: str | None = None) -> dict[str, Any]:
    """Saves answers as review decisions (one per question, so a later answer
    never erases an earlier one); returns the new status."""
    from review.state import record_decision

    specs = {key: (label, spec) for key, label, _who, spec, _req in QUESTIONS}
    for key, raw in values.items():
        if key not in specs:
            raise ValueError(f"{key} is not an I-360 question.")
        label, spec = specs[key]
        item = {"id": f"i360:{key}", "kind": "i360", "level": "review", "title": label, "group": "attorney", "actions": ["set", "blank"],
                "facts": [{"key": key, "input": spec}]}
        decision = {"action": "blank" if raw in ("", None) else "set", "values": {} if raw in ("", None) else {key: raw},
                    "reviewer": reviewer, **({"role": role} if role else {}), "note": "I-360 question"}
        record_decision(client_dir, item, decision)
    return status(client_dir)
