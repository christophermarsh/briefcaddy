"""Learning from corrections: across every client, how often reviewers
confirm what the pipeline filled in, and how often they correct it -- by
I-485 field and by where the value came from (a document reader, the
handwriting reader, the portal, a rule). The fields at the top of the list
are where the next improvement to the readers pays off most.

Built from what is already on disk: each client's decisions.json (what the
reviewer did) against fact_graph.json (what the pipeline produced before
review). Nothing is sent anywhere.

    python src/review/learning.py            # the top of the list, in the terminal
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from review.state import _read  # noqa: E402
import clock  # noqa: E402
import schema_path

SOURCE_NAMES = {
    "intake_questionnaire": "Paper questionnaire (scan)", "portal_questionnaire": "Client portal answers", "firm_profile": "Firm profile",
    "passport": "Passport reader", "i94": "I-94 reader", "i360_approval": "I-360 approval reader", "birth_certificate": "Birth certificate reader",
    "ssn_card": "SSN card reader", "drivers_license": "Driver's license reader", "work_permit": "Work permit reader", "visa": "Visa reader",
    "notice_to_appear": "NTA reader", "i213": "DHS record (I-213) reader","uscis_notice": "USCIS notice reader", "criminal_record": "Court record reader", "sij_order": "SIJ court order reader", "us_passport": "U.S. passport reader",
    "citizenship_certificate": "Citizenship certificate reader", "green_card": "Green card reader", "us_birth_certificate": "U.S. birth certificate reader",
    "tax_return": "Tax return reader",
}


def _norm(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def _source(fact: dict[str, Any] | None) -> str:
    if not fact:
        return "Nothing (left empty)"
    if fact.get("derived_by"):
        rule = str(fact["derived_by"])
        return f"Firm policy {rule.split(':', 1)[1]}" if rule.startswith("POLICY:") else f"Rule {rule}"
    types = sorted({s.get("doc_type") for s in fact.get("sources") or [] if s.get("doc_type")})
    return " + ".join(SOURCE_NAMES.get(t, t.replace("_", " ").capitalize()) for t in types) or "Unknown"


def outcomes(client_dir: Path) -> list[dict[str, Any]]:
    """One row per fact a reviewer decided on: what the pipeline had, what the
    reviewer kept, and which of confirmed / corrected / filled / blanked."""
    graph = (_read(client_dir / "fact_graph.json", {}) or {}).get("facts", {})
    rows = []
    for item_id, d in _read(client_dir / "decisions.json", {}).items():
        if d.get("undone"):
            continue  # reopened: the reviewer took it back, so it says nothing yet (review/state.py undo_decision)
        action = d.get("action")
        if action == "acknowledge":
            continue  # an alert read and signed off: nothing to be right or wrong about
        if (d.get("item") or {}).get("kind") in ("declaration", "part14"):
            continue  # a declaration paragraph edited (src/drafting.py), a Part 14 explanation written or approved (src/part14_explain.py): not a box a reader filled
        for key in (d.get("item") or {}).get("facts", []):
            fact = graph.get(key)
            before = fact.get("value") if fact and fact.get("status") == "resolved" else None
            if action == "confirm":
                outcome, after = "confirmed", before
            elif action == "blank":
                outcome, after = ("blanked" if before not in (None, "") else "left empty"), None
            elif action == "set" and key in (d.get("values") or {}):
                after = d["values"][key]
                outcome = "filled" if before in (None, "") else "confirmed" if _norm(after) == _norm(before) else "corrected"
            else:
                continue
            if outcome == "confirmed" and before in (None, ""):
                continue  # confirming an empty box says nothing about a reader
            rows.append({"client": client_dir.name, "key": key, "item": item_id, "outcome": outcome, "before": before, "after": after,
                         "source": _source(fact if before not in (None, "") else None), "kind": (d.get("item") or {}).get("kind"),
                         "reviewer": d.get("reviewer"), "at": d.get("at"), "note": d.get("note") or ""})
    return rows


def _tally(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = {o: sum(1 for r in rows if r["outcome"] == o) for o in ("confirmed", "corrected", "filled", "blanked")}
    judged = n["confirmed"] + n["corrected"] + n["blanked"]
    return n | {"decided": len(rows), "error_rate": round((n["corrected"] + n["blanked"]) / judged, 3) if judged else None}


def learning(data_root: Path, label=None, exclude=()) -> dict[str, Any]:
    """label(fact_key) -> the field's name on the form (Catalog.label). exclude: case ids left out (restricted cases the
    reader may not see, src/restricted.py: their values are not anyone else's examples)."""
    clients = [p for p in sorted(data_root.iterdir()) if (p / "decisions.json").exists() and (p / "fact_graph.json").exists() and p.name not in exclude]
    rows = [r for c in clients for r in outcomes(c)]
    by_field: dict[str, list] = {}
    by_source: dict[str, list] = {}
    for r in rows:
        by_field.setdefault(r["key"], []).append(r)
        by_source.setdefault(r["source"], []).append(r)
    fields = []
    for key, rs in by_field.items():
        wrong = [r for r in rs if r["outcome"] in ("corrected", "blanked")]
        fields.append({"key": key, "label": label(key) if label else key, **_tally(rs),
                       "sources": sorted({r["source"] for r in rs}),
                       "examples": [{"client": r["client"], "before": r["before"], "after": r["after"], "source": r["source"]} for r in wrong[:5]]})
    # most wrong answers first, then the most often left for a person to fill in
    fields.sort(key=lambda f: (-(f["corrected"] + f["blanked"]), -f["filled"], -f["decided"]))
    sources = sorted(({"source": s, **_tally(rs)} for s, rs in by_source.items()), key=lambda s: -(s["corrected"] + s["blanked"]))
    return {"clients": len(clients), "decisions": len(rows), "overall": _tally(rows), "fields": fields, "sources": sources}


ACCURACY_METHOD = [
    "Counted from the review decisions on file: every value a reviewer decided on, on every client, dated by when the decision was made.",
    "Confirmed: the reviewer kept the value the system had filled in (confirmed it, or typed the same value).",
    "Corrected: the reviewer replaced a filled-in value with a different one.",
    "Left blank: the reviewer removed a filled-in value, so the box stays empty.",
    "Corrected (box was empty): the system had no value and the reviewer entered one. The Decision log calls it Corrected too; it is shown apart here because nothing was filled in to be right or wrong.",
    "Not counted: alerts a reviewer acknowledged (nothing to be right or wrong about), confirming or leaving blank a box that was "
    "already empty, and decisions that were undone (until they are made again).",
    "Where the value came from is the document reader, the client's answers, the rule or the firm's policy that filled it in "
    "before review. A value read from two kinds of document counts once, under both names together.",
    "Every number is a count of decisions. Nothing is estimated or sampled; a period or an item without decisions says no data.",
]
# src/compare.py scores a filled form against the firm's own hand-filled form for the same client. Those comparisons are not
# decisions, so they are not counted in these tables: src/accuracy.py runs them each night and the page shows them in a section of their own.
ACCURACY_REFERENCE = ("No data here from hand-filled references: they are compared each night and counted in their own section "
                      "(Against hand-filled references), never mixed into the review decisions.")


def accuracy(data_root: Path, start: str | None = None, end: str | None = None, label=None, ref=None) -> dict[str, Any]:
    """Per source and per form item: how many filled values reviewers confirmed, corrected or left blank between start and
    end (ISO dates, both included), from the decisions on file (outcomes). label(fact_key) / ref(fact_key): the form's
    wording and its Part/Item. Counts only: nothing estimated."""
    for name, value in (("from", start), ("to", end)):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"Choose a {name} date from the calendar.")  # the page's date picker sends YYYY-MM-DD
    if start and end and start > end:
        raise ValueError("The period ends before it starts.")
    clients = [p for p in sorted(data_root.iterdir()) if (p / "decisions.json").exists() and (p / "fact_graph.json").exists()]
    rows = [r for c in clients for r in outcomes(c)
            if r["outcome"] != "left empty"  # "leave blank" on a box that was already empty: nothing filled to judge
            and (not start or clock.day(r["at"]) >= start) and (not end or clock.day(r["at"]) <= end)]  # the office's dates

    def tally(rs: list[dict[str, Any]]) -> dict[str, int]:
        n = {o: sum(1 for r in rs if r["outcome"] == o) for o in ("confirmed", "corrected", "blanked", "filled")}
        return n | {"reviewed": len(rs)}

    by_source: dict[str, list] = {}
    by_item: dict[str, list] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)
        by_item.setdefault(r["key"], []).append(r)
    sources = sorted(({"label": s, **tally(rs)} for s, rs in by_source.items()), key=lambda g: (-g["reviewed"], g["label"]))
    items = sorted(({"key": k, "label": label(k) if label else k, "ref": ref(k) if ref else "", **tally(rs)} for k, rs in by_item.items()),
                   key=lambda g: (-(g["corrected"] + g["blanked"]), -g["reviewed"], g["label"]))
    return {"from": start, "to": end, "clients": len({r["client"] for r in rows}), "totals": tally(rows),
            "first": min((r["at"] for r in rows if r["at"]), key=clock.key, default=None), "last": max((r["at"] for r in rows if r["at"]), key=clock.key, default=None),
            "by_source": sources, "by_item": items, "method": ACCURACY_METHOD, "reference": ACCURACY_REFERENCE}


def main() -> None:
    from fill import load_field_map
    from review.state import Catalog

    repo = Path(__file__).resolve().parents[2]
    field_map = load_field_map(schema_path.path("field_map", "i485", schema_path.schemas_in(repo)))
    catalog = Catalog(field_map, schema_path.path("template", "i485", schema_path.schemas_in(repo)))
    report = learning(repo / "data" / "clients", catalog.label)
    o = report["overall"]
    print(f"{report['clients']} clients, {report['decisions']} reviewed answers: {o['confirmed']} confirmed, {o['corrected']} corrected, "
          f"{o['blanked']} removed, {o['filled']} corrected where the box was empty")
    print("\nBy source (corrected + removed / judged):")
    for s in report["sources"]:
        rate = f"{s['error_rate']:.0%}" if s["error_rate"] is not None else "--"
        print(f"  {s['source'][:48]:48} {rate:>5} wrong   ({s['corrected']} corrected, {s['blanked']} removed, {s['confirmed']} confirmed)")
    print("\nFields corrected most often:")
    for f in report["fields"][:20]:
        if not (f["corrected"] or f["blanked"] or f["filled"]):
            break
        print(f"  {f['label'][:60]:60} {f['corrected']} corrected, {f['blanked']} removed, {f['filled']} filled, {f['confirmed']} confirmed")


if __name__ == "__main__":
    main()
