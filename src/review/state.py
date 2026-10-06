"""Review state for one client: the pipeline's output bundle, the
reviewer's decisions, and everything derived from the two.

On disk, per client (data/clients/<id>/, gitignored like all client data):
  fact_graph.json           pipeline output -- never modified by review
  reading_flags.json        questionnaire items the readers couldn't read
  evidence.json             where each questionnaire answer sits on the scan
  meta.json                 client id, source folder
  documents.json            one record per document (src/documents.py)
  decisions.json            the reviewer's decisions (this module writes it)
  fact_graph_reviewed.json  fact_graph.json + decisions   (regenerated)
  i485_filled.pdf, flag_report.txt                         (regenerated)

Decisions are applied on top of a fresh copy of the pipeline's graph every
time, so an undone decision simply stops being applied, and re-running the
pipeline never loses review work that still applies. decisions.json keeps
every decision ever made on an item: the current one's fields at the top
of its entry (what every reader uses), the whole chain under "history", and
an undo marks the entry ("undone": who, when) instead of deleting it. Nothing is
overwritten: each decision lands in the fact graph as its own source /
review record (FactGraph.sign_off / set_by_review / blank_by_review).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import arrival
import documents
from batch import ClientResult, cross_check, finalize_client
from factgraph import FactGraph
from fill import field_max_lengths, map_facts_to_fields
from validate import Flag, render_report, validate_graph
import clock
import events
import schema_path
import read_scope

ACTIONS = ("confirm", "set", "blank", "acknowledge", "absent")
ACTION_WORDS = {"confirm": "Confirmed", "set": "Corrected", "blank": "Left blank", "acknowledge": "Acknowledged",
                "absent": "Marked absent"}  # the ledger's words (src/events.py); absent: the client has no such paper (src/absence.py)
READING_FLAG_KINDS = ("unread", "blank_template", "unsupported_language")


# --- bundle ---------------------------------------------------------------


def save_bundle(result: ClientResult, out_dir: str | Path, source_folder: str | Path) -> None:
    """Everything the review app needs, written after the pipeline runs."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    import document_instances
    document_instances.invalidate(out, result.boundary_plans)
    if result.boundary_plans and result.documents is None:
        raise ValueError("Document evidence records are missing; processing has not completed.")
    document_instances.stage(out, result.boundary_plans)
    paths = [out / name for name in ("fact_graph.json", "fact_graph_raw.json", "reading_flags.json", "evidence.json", "meta.json")]
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        result.graph.save(out / "fact_graph.json")
        if result.raw_graph is not None:
            result.raw_graph.save(out / "fact_graph_raw.json")
        reading = [asdict(f) for f in result.review_flags if f.kind in READING_FLAG_KINDS]
        _write(out / "reading_flags.json", reading)
        _write(out / "evidence.json", result.evidence)
        _write(out / "meta.json", {
            "client_id": result.graph.client_id,
            "source_folder": str(Path(source_folder).resolve()),
            "processed_at": clock.stamp(),
            "classifications": {k: v.doc_type for k, v in result.classifications.items()},
            "errors": result.errors,
            "derivation": {"rules": result.rules_used, "policies": result.policies_used},
        })
        if result.documents is not None:  # a full save clears the durable processing marker
            documents.save_run(out, result.documents)
    except Exception:
        # Preserve the last completed bundle while its durable marker keeps it
        # out of accepted output. A process crash still leaves that marker.
        for path, content in before.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    events.record("facts", "read", f"Read {len(result.classifications)} documents into {len(result.graph.all_facts())} facts", case_dir=out,
                  default_who=("The document reader", "system", "system"))


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


# --- decisions --------------------------------------------------------------


def load_decisions(client_dir: Path) -> dict[str, dict[str, Any]]:
    """The decisions in force: an undone one is kept on file (load_decision_log) but no longer counts."""
    import document_instances
    aliases = document_instances.held_aliases(client_dir)
    affected = set()
    if aliases and (client_dir / "fact_graph.json").exists():
        affected = document_instances.without_sources(FactGraph.load(client_dir / "fact_graph.json"), aliases)
    affected = set(affected) | __import__('subject_attribution').affected_keys(client_dir)
    decisions = {iid: d for iid, d in load_decision_log(client_dir).items() if not d.get("undone")
                 and (document_instances.independent_absence(iid, d) or not set(d.get("item", {}).get("facts", [])) & affected)}
    return __import__('critical_review').filter_decisions(client_dir, decisions)


def load_decision_log(client_dir: Path) -> dict[str, dict[str, Any]]:
    """Every item ever decided, undone ones included, each with its "history" chain."""
    return _read(client_dir / "decisions.json", {})


def _history(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    """An entry's chain of decisions, oldest first. An entry written before the chain was kept counts as its one decision."""
    if not entry:
        return []
    if entry.get("history"):
        return [dict(h) for h in entry["history"]]
    return [{k: v for k, v in entry.items() if k not in ("item", "history", "undone")}
            | ({"undone": entry["undone"]} if entry.get("undone") else {})]


def prepare_decision(client_dir: Path, item: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Validates decision against item (the current review item it answers)
    and stores it. Raises ValueError with a reviewer-facing message."""
    action = decision.get("action")
    reviewer = (decision.get("reviewer") or "").strip()
    note = (decision.get("note") or "").strip()
    if not reviewer:
        raise ValueError("Enter your name first: every decision records who made it.")
    if action not in item["actions"]:
        raise ValueError(f"'{action}' isn't possible for this item (allowed: {', '.join(item['actions'])}).")
    if action == "acknowledge" and item["level"] == "blocking" and not note:
        raise ValueError("A blocking item needs a note explaining why it is OK to proceed.")
    values: dict[str, Any] = {}
    if action == "set":
        inputs = {f["key"]: f["input"] for f in item["facts"] if not f.get("comparison_readonly")}
        for key, raw in (decision.get("values") or {}).items():
            if key not in inputs:
                raise ValueError(f"{key} is not part of this item.")
            value = (raw or "").strip() if isinstance(raw, str) else raw
            if value in ("", None):
                continue
            values[key] = _check_value(value, inputs[key], key)
        if not values:
            raise ValueError("Enter at least one value (or choose 'Leave blank').")
    stored = {
        "action": action,
        "values": values,
        "reviewer": reviewer,
        **({"role": decision["role"]} if decision.get("role") else {}),  # the signed-in person's role
        **({"old": decision["old"]} if decision.get("old") is not None else {}),  # the text it replaced (a declaration paragraph: src/drafting.py)
        **({"absence": decision["absence"]} if action == "absent" and decision.get("absence") else {}),  # which paper, why (src/absence.py)
        # the names card: the name-setting documents the choice was made over; one that arrives later reopens the card (src/name_events.py)
        **({"over": sorted({e["doc"] for e in item["timeline"].get("events", []) if e.get("setter")})}
           if item.get("kind") == "names" and item.get("timeline") and action == "set" else {}),
        **({"name_evidence": item["timeline"].get("marriage_evidence")}
           if item.get("kind") == "names" and item.get("timeline") and action == "set" else {}),
        "note": note,
        "at": clock.stamp(),
        "item": {k: item[k] for k in ("id", "kind", "level", "title", "group")} | {"facts": [f["key"] for f in item["facts"]]},
    }
    if action in {"confirm", "set", "blank"}:
        import document_instances
        import subject_attribution
        import critical_review
        affected = subject_attribution.affected_keys(client_dir)
        aliases = document_instances.held_aliases(client_dir)
        if aliases and (client_dir / "fact_graph.json").exists():
            affected |= document_instances.without_sources(FactGraph.load(client_dir / "fact_graph.json"), aliases)
        touched = set(values) if action == "set" else set(stored["item"]["facts"])
        if touched & affected:
            raise ValueError("Review document boundaries and whose facts these are in Documents first.")
        proof = critical_review.confirmation(client_dir, stored["item"], stored | {
            "evidence_fingerprints": decision.get("evidence_fingerprints")})
        if proof:
            stored["evidence_confirmation"] = proof
    return stored


def record_prepared_decision(client_dir: Path, item: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """Persist an internally prepared action under the caller's case lock."""
    decisions = load_decision_log(client_dir)
    # appended, never overwritten: an item decided, undone and decided again keeps all three steps
    history = _history(decisions.get(item["id"])) + [{k: v for k, v in stored.items() if k != "item"}]
    decisions[item["id"]] = stored | {"history": history}
    _write(client_dir / "decisions.json", decisions)
    verb = ACTION_WORDS[stored["action"]]  # the ledger names the fact, never the value (src/events.py)
    events.record("decisions", verb.lower().replace(" ", "_"), f"{verb}: {events.item_words(item)}",
                  case_dir=client_dir, who=stored["reviewer"], role=stored.get("role"))
    _reader_examples(lambda m: m.from_decision(client_dir, stored))  # a confirm or a correction of a read value: the firm's labelled example (brief M1)
    return stored


def record_decision(client_dir: Path, item: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    import jobs
    with jobs.case_lock(jobs.folder_for(client_dir.parent), client_dir.name):
        return record_prepared_decision(client_dir, item, prepare_decision(client_dir, item, decision))


def _reader_examples(write) -> None:
    """The firm's labelled examples (src/reader_examples.py) are a by-product of a decision: one that cannot be written never stops the decision."""
    try:
        import reader_examples

        write(reader_examples)
    except Exception:  # noqa: BLE001 -- the decision is on file; the example is not worth a failed Save
        pass


def undo_decision(client_dir: Path, item_id: str, by: str = "", role: str | None = None, lifted: str | None = None) -> None:
    """Reopens the item: its current decision is marked undone (who, when), kept on file, and no longer applied. lifted: the sentence for the
    ledger when the product itself ends the decision (the paper the office marked absent arrived: src/absence.py), else "Reopened"."""
    decisions = load_decision_log(client_dir)
    entry = decisions.get(item_id)
    if not entry or entry.get("undone"):
        return
    mark = {"by": (by or "").strip(), **({"role": role} if role else {}), "at": clock.stamp(), **({"why": lifted} if lifted else {})}  # why: the Decision log says it
    history = _history(entry)
    history[-1] = history[-1] | {"undone": mark}
    decisions[item_id] = entry | {"undone": mark, "history": history}
    _write(client_dir / "decisions.json", decisions)
    _reader_examples(lambda m: m.withdraw(client_dir, item_id, entry.get("at"), mark))  # its examples are counted nowhere from now on
    events.record("decisions", "lifted" if lifted else "undone", lifted or f"Reopened: {events.item_words(entry.get('item') or {})}",
                  case_dir=client_dir, who=(by or "").strip() or None, role=role)


def _check_value(value: Any, spec: dict[str, Any], key: str) -> Any:
    if spec["type"] == "choice" and value not in spec["options"]:
        raise ValueError(f"{key}: choose one of {', '.join(spec['options'])}.")
    if spec["type"] == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{key}: not a valid date.") from None
    if spec.get("pattern") and not re.match(spec["pattern"], str(value)):
        raise ValueError(f"{key}: expected the form {spec.get('placeholder', spec['pattern'])}.")
    if spec.get("maxlen") and len(re.sub(r"\D", "", str(value)) if spec.get("digits") else str(value)) > spec["maxlen"]:
        raise ValueError(f"{key}: the form allows at most {spec['maxlen']} characters here.")
    if spec["type"] == "text" and not spec.get("multiline"):
        value = value.upper()  # the firm fills the I-485 in capitals; a written account (multiline) keeps the client's own case
    return value


def apply_decisions(graph: FactGraph, decisions: dict[str, dict[str, Any]]) -> None:
    """Every decision onto the graph. A confirm, a set, a blank and an absence mark each carry the decision's own date and time ("at") as the
    review's resolved_at, never the time the case was last read again (a name a person types is dated by it: brief K6)."""
    for d in decisions.values():
        reviewer, note, keys = d["reviewer"], d.get("note", ""), d["item"]["facts"]
        touched = []
        if d["action"] == "confirm":
            for key in keys:
                fact = graph.get(key)
                if fact is not None and fact.status == "resolved":
                    proof = d.get("evidence_confirmation")
                    entries = proof.get("keys") if isinstance(proof, dict) else None
                    selected = entries.get(key) if isinstance(entries, dict) else None
                    if isinstance(selected, dict) and ("chosen_value" not in selected or fact.value != selected["chosen_value"]):
                        # Raw input can differ from the displayed derived
                        # value. Defer this sign-off until the post-derive
                        # pass actually produces the source-reviewed value;
                        # never insert a value from the approval itself.
                        continue
                    touched.append(graph.sign_off(key, reviewer, note))
        elif d["action"] == "set":
            for key, value in d["values"].items():
                touched.append(graph.set_by_review(key, value, reviewer, note))
            if d.get("over") is not None:  # the names card: what was on it when the person chose (src/name_events.py OVER_KEY)
                touched.append(graph.set_by_review("applicant.name_choice_documents", json.dumps(d["over"]), reviewer, note))
            if "name_evidence" in d:
                touched.append(graph.set_by_review("applicant.name_choice_evidence", d["name_evidence"], reviewer, note))
        elif d["action"] == "blank":
            for key in keys:
                touched.append(graph.blank_by_review(key, reviewer, note))
        elif d["action"] == "absent":  # the client has no such paper: the marker src/absence.py marked_value reads when a form is filled (the case's facts stay empty)
            said = d.get("absence") or {}
            touched.append(graph.set_by_review(f"case.absent.{said.get('paper')}", said.get("reason"), reviewer, note))
        # "acknowledge" changes no fact -- see _acknowledge()
        for fact in touched:
            if d.get("at") and fact is not None and fact.review is not None:
                fact.review.resolved_at = d["at"]


# --- flags after review -------------------------------------------------------


def _open_reading_flags(client_dir: Path, decisions: dict) -> list[Flag]:
    flags = [Flag(**f) for f in _read(client_dir / "reading_flags.json", [])]
    out = []
    for f in flags:
        decision = decisions.get(item_id(f))
        if decision and decision["action"] in ("set", "blank"):
            continue  # the reviewer supplied (or blanked) the answer
        out.append(f)
    return out


def _acknowledge(flags: list[Flag], decisions: dict) -> list[Flag]:
    out = []
    for f in flags:
        d = decisions.get(item_id(f))
        if d and d["action"] == "acknowledge":
            note = f": {d['note']}" if d["note"] else ""
            f = Flag("informational", f.fact_key, f"ACKNOWLEDGED by {d['reviewer']}{note}: {f.message}", f.kind)
        out.append(f)
    return out


def _max_lengths(template_path: str) -> dict[str, int]:
    return field_max_lengths(template_path)  # kept by the template cache, which reads the file again when it is replaced (fill/template_cache.py)


def reviewed_graph(client_dir: Path, assume: dict[str, Any] | None = None, aside: bool = True, g28_card: bool = True,
                   decisions: dict[str, dict[str, Any]] | None = None) -> FactGraph:
    """assume: {fact key: value} answers taken as settled for this one reading, never saved (pending_items asks what settling
    an open answer would add). g28_card: the G-28 card's choice for the I-485's and I-765's mailing address laid over the case (src/g28.py); the card
    itself reads the case without it. decisions: the decisions to apply instead of the case's own ({} reads the case as the product alone
    would have filled it: the audit of what the office changes, src/audit_fill.py). The pipeline's facts with every decision applied. When the raw graph
    is saved, derivation is re-run on top of the decisions -- so a corrected
    input (the address line a reviewer typed in) flows into the boxes built
    from it (prior address, Part 14) -- and the decisions are applied again
    so sign-offs and corrections of derived boxes stick."""
    import absence
    import document_instances

    if decisions is None:
        decisions = absence.without_arrived(client_dir, load_decisions(client_dir))  # a paper marked absent that has arrived is not an absence
    raw_path = client_dir / "fact_graph_raw.json"
    meta = _read(client_dir / "meta.json", {})
    if not raw_path.exists() or "derivation" not in meta:
        graph = FactGraph.load(client_dir / "fact_graph.json")
        document_instances.without_sources(graph, document_instances.held_aliases(client_dir))
        __import__('subject_attribution').filter_graph(graph, client_dir)
        office_answered(graph, client_dir)
        apply_decisions(graph, decisions)
        if graph.get(arrival.DATE) is None:  # a graph saved before the settled date existed (and never derived again): item 10's date still fills
            arrival.settle_date(graph)
        return __import__('critical_review').mark_graph(
            _explained(_office(_cuban(_asylee(_family(graph), client_dir), client_dir), client_dir, g28_card), client_dir, decisions), decisions)
    from batch import derive

    graph = FactGraph.load(raw_path)
    document_instances.without_sources(graph, document_instances.held_aliases(client_dir))
    __import__('subject_attribution').filter_graph(graph, client_dir)
    office_answered(graph, client_dir)
    apply_decisions(graph, decisions)
    for key, value in (assume or {}).items():
        graph.set_by_review(key, value, "pending")
    rules, policies = _derivation(meta["derivation"])
    derive(graph, rules, policies)
    apply_decisions(graph, decisions)
    return __import__('critical_review').mark_graph(
        _explained(_office(_cuban(_asylee(_family(graph), client_dir), client_dir), client_dir, g28_card), client_dir, decisions), decisions)


def _explained(graph: FactGraph, client_dir: Path, decisions: dict[str, dict[str, Any]]) -> FactGraph:
    """The Part 14 explanations an attorney approved for this case's Part 9 answers, as entries on the form's own Part 14 page, after the ones
    the questionnaire made (src/part14_explain.py)."""
    import part14_explain

    return part14_explain.into_graph(graph, client_dir, decisions)


# The client portal behind each folder of cases, for the office's questions the client answered there: the review app registers
# its own (ReviewApp: --portal); otherwise PORTAL_DATA, else the portal folder beside the case folders (data/clients -> data/portal).
PORTALS: dict[Path, Path] = {}
OFFICE_DOC_ID = "office question"  # batch.process_documents' name for the source


def _portal_for(client_dir: Path) -> Path:
    import os

    root = client_dir.parent.resolve()
    if root in PORTALS:
        return PORTALS[root]
    return Path(os.environ["PORTAL_DATA"]) if os.environ.get("PORTAL_DATA") else root.parent / "portal"


@lru_cache(maxsize=1)
def _i485_field_map() -> dict:
    from fill import load_field_map

    return load_field_map(schema_path.path("field_map", "i485"))


def office_answered(graph: FactGraph, client_dir: Path) -> list[str]:
    """The client's typed answers to the office's questions (portal/questions.py office_answers), added to the case as it is read,
    the moment the client sends them. Before, they reached the case only when the portal's worker processed the client again
    (portal/engine.process_client): a case read before that pass, or one the overnight run builds from a folder, kept the box empty
    while the card's source line already showed the answer (docs/research/buyer_walkthrough_4.md, finding 2). An answer the graph
    already holds from that pass is replaced, never doubled; the newest answer to a box wins; a decision still applies on top.
    Returns the fact keys it filled."""
    from portal.questions import office_answers
    from portal.store import CLIENT_ID

    if not CLIENT_ID.fullmatch(client_dir.name):
        return []
    path = _portal_for(Path(client_dir)) / "clients" / client_dir.name / "requests.json"
    try:
        requests = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        return []  # the portal's file mid-write or unreadable: the answer arrives on the next reading
    filled = []
    for extracted in office_answers(requests if isinstance(requests, list) else [], _i485_field_map()):
        fact = graph.get(extracted.fact_key)
        if fact is not None and any(s.doc_id == OFFICE_DOC_ID for s in fact.sources):
            fact.sources = [s for s in fact.sources if s.doc_id != OFFICE_DOC_ID]
        graph.add_source(extracted.fact_key, doc_id=OFFICE_DOC_ID, doc_type="office_question", raw_value=extracted.raw_value,
                         normalized_value=extracted.normalized_value, confidence=extracted.confidence, tier=3)
        filled.append(extracted.fact_key)
    return filled


def _office(graph: FactGraph, client_dir: Path, g28_card: bool = True) -> FactGraph:
    """The firm's details from the case's office (src/offices.py): its attorney, address and accounts on every form; then, where a person made one on
    the case's G-28 card, the choice of the mailing address for the I-485 and the I-765 (src/g28.py)."""
    import offices

    graph = offices.apply(graph, client_dir)
    if g28_card:
        import g28

        graph = g28.apply_mailing(graph, client_dir)
    return graph


def _asylee(graph: FactGraph, client_dir: Path) -> FactGraph:
    """An asylee or a refugee (a grant in the case): the I-485's Part 2 category, the grant date and the exemptions (src/asylee.py)."""
    from asylee import case_facts

    return case_facts(graph, client_dir)


def _cuban(graph: FactGraph, client_dir: Path) -> FactGraph:
    """The Cuban Adjustment Act or a HRIFA dependent: the I-485's Part 2 category and item 2, and the exemptions (src/cuban_adjustment.py)."""
    from cuban_adjustment import case_facts

    return case_facts(graph, client_dir)


def _family(graph: FactGraph) -> FactGraph:
    """A family-based case (the petitioner's status is known): the facts the
    family forms and the I-485's Part 2 category build from (src/family.py).
    Any other case is left as it is."""
    status = graph.get("petitioner.status")
    if status is None or status.status != "resolved":
        return graph
    from family import derive as family_derive

    return family_derive(graph)


@lru_cache(maxsize=4)
def _derivation_cached(rule_ids: tuple, policies_used: bool, _firm: tuple):
    from rules import ALL_RULES
    from rules.policy import load_policy_profile

    rules = [r for r in ALL_RULES if r.rule_id in rule_ids]
    policies = load_policy_profile(schema_path.path("law", "policy_sijs")) if policies_used else None
    return rules, policies


def _derivation(spec: dict) -> tuple:
    from rules import firm_policies

    # the firm's edits to its policies (Settings) are read again the moment they change: the key carries the file's stamp
    return _derivation_cached(tuple(spec.get("rules") or ()), bool(spec.get("policies")), firm_policies.stamp())


def current_flags(client_dir: Path, field_map: dict, template_path: str | Path, assume: dict[str, Any] | None = None) -> tuple[FactGraph, list[Flag]]:
    """Flags as they stand with every decision applied -- without writing a
    PDF, so the review queue can be rebuilt after every click."""
    from batch import field_to_fact

    decisions = load_decisions(client_dir)
    graph = reviewed_graph(client_dir, assume)
    mapping = map_facts_to_fields(graph, field_map)
    limits = _max_lengths(str(template_path))
    to_fact = field_to_fact(field_map)
    overflow = [
        Flag("review", to_fact.get(name, name),
             f"{to_fact.get(name, name)}: value {value!r} is longer than the form allows ({limits[name]} characters): enter a shorter value.",
             kind="overflow")
        for name, value in mapping.values.items()
        if name in limits and isinstance(value, str) and len(value) > limits[name]
    ]
    from batch import part14_spot_flags

    flags = validate_graph(graph, []) + _open_reading_flags(client_dir, decisions) + cross_check(graph) + overflow + part14_spot_flags(graph)
    import document_instances
    boundaries = [Flag("blocking", "documents", message, kind="document_boundary") for message in document_instances.problems(client_dir)]
    boundaries += [Flag("blocking", "documents", message, kind="document_subject")
                   for message in __import__('subject_attribution').problems(client_dir)]
    return graph, _acknowledge(flags, decisions) + boundaries


def refill(client_dir: Path, field_map: dict, template_path: str | Path) -> dict[str, Any]:
    """Re-fills the I-485 and re-renders the flag report from the pipeline's
    graph plus every decision. Seconds -- no OCR or model calls."""
    decisions = load_decisions(client_dir)
    graph = reviewed_graph(client_dir)
    result = ClientResult(graph=graph, review_flags=_open_reading_flags(client_dir, decisions) + cross_check(graph))
    filing = finalize_client(result, template_path, field_map, [], client_dir)
    flags = _acknowledge(filing.flags, decisions)
    import document_instances
    flags += [Flag("blocking", "documents", message, kind="document_boundary") for message in document_instances.problems(client_dir)]
    flags += [Flag("blocking", "documents", message, kind="document_subject")
              for message in __import__('subject_attribution').problems(client_dir)]
    (client_dir / "flag_report.txt").write_text(render_report(graph.client_id, flags), encoding="utf-8")
    graph.save(client_dir / "fact_graph_reviewed.json")
    counts = {level: sum(1 for f in flags if f.level == level) for level in ("blocking", "review", "informational")}
    return {"pdf": str(filing.filled_pdf_path), "counts": counts, "refilled_at": clock.stamp()}


# --- review items ---------------------------------------------------------


def item_id(flag: Flag) -> str:
    return f"{flag.kind}:{flag.fact_key}"


_SPLIT_YEAR = re.compile(r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, )(19|20) (\d\d)\b")


def tidy_tooltip(text: str) -> str:
    """A field's tooltip as the I-485's PDF stores it, with its typesetting breaks closed: USCIS's file spells a year "19 97"
    ("Since April 1, 19 97, have you...") and DHS "D H S"; on screen they read as plain "1997" and "DHS"."""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\bD H S\b", "DHS", _SPLIT_YEAR.sub(r"\1\2\3", text))


class Catalog:
    """Static, per-server knowledge used to describe items: a human label
    for every fact key (the I-485's own tooltip text for the field it
    fills), its input type, and the 'why' of each policy."""

    def __init__(self, field_map: dict, template_path: str | Path, policies: list[dict] | None = None):
        from fill import template_cache

        self.field_map = field_map
        self.tooltips = {name: tidy_tooltip(str(f.get("/TU") or "")) for name, f in template_cache.fields(template_path).items()}
        self.limits = _max_lengths(str(template_path))
        self._policies = policies or []

    @property
    def policy_why(self) -> dict[str, str]:
        """What the screen says a policy does: its plain text (schemas/law/policy_sijs.json, or the firm's own wording over it, src/rules/firm_policies.py),
        else its older "why"."""
        from rules import firm_policies

        log = firm_policies.edits()
        return {f"POLICY:{p['id']}": (m := firm_policies.effective(p, log)).get("plain_text") or m.get("why", "") for p in self._policies}

    def _fields(self, fact_key: str) -> list[str]:
        """The AcroForm names a fact fills -- read from the spec itself, not its JSON text (a name like "Pt8Line42\\.a" keeps one backslash)."""
        out: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, str):
                if node.startswith("form1["):
                    out.append(node)
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(self.field_map.get(fact_key))
        return out

    def label(self, fact_key: str) -> str:
        if fact_key in LABEL_OVERRIDES:
            return LABEL_OVERRIDES[fact_key][0]
        fields = self._fields(fact_key)
        if not fields:
            return CONTEXT_LABELS.get(fact_key, fact_key.rsplit(".", 1)[-1].replace("_", " ").capitalize())  # not on the I-485
        tip = self.tooltips.get(fields[0], "")
        tip = re.sub(r"\s*Select [^.]*\.?\s*$", "", tip)
        tip = re.sub(r"\.?\s*Enter as 2-digit Month, 2-digit Day,? and 4-digit Year\.?", "", tip)  # an instruction, never part of a label
        tip = re.sub(r"\(select only one box\)|This is a drop-?down.*$", "", tip)
        # Boilerplate the I-485 repeats in every tooltip of a Part.
        tip = re.sub(r"\((Person|individual) applying for lawful permanent residence\)\.?", "", tip)
        tip = re.sub(r"Information About You\b\.?\s*|General Eligibility and Inadmissibility Grounds\.\s*", "", tip)
        tip = re.sub(r"\s+", " ", tip).strip(" .")
        if len(tip) > 200:
            # Some tooltips carry a whole section preamble (Part 9 item 22's
            # is ~900 characters); keep the numbered question itself.
            # The question starts at the last "NN. Capitalized" ("22. Have
            # you EVER..."), not at "22. - 41., you must..." in the preamble.
            starts = [m.start() for m in re.finditer(r"\b\d{1,2}\s?[A-Z]?\.\s+[A-Z][a-z]", tip)]
            # prefer the numbered phrase that is the question itself ("74. Since
            # April 1, 1997, have you...?"), not a trailing "Part 14. Additional
            # Information" pointer
            questions = [i for i in starts if "?" in tip[i:]]
            if starts:
                tip = tip[(questions or starts)[-1]:]
        return tip or fact_key

    def ref(self, fact_key: str) -> str:
        """Part/Item from the field's untrimmed tooltip -- the trimmed label
        can start at a pointer like "14. Additional Information"."""
        if fact_key in LABEL_OVERRIDES:
            return LABEL_OVERRIDES[fact_key][1]
        fields = self._fields(fact_key)
        return ref_by_name(i485_ref(self.tooltips.get(fields[0], "")), fields[0]) if fields else ""

    def input(self, fact_key: str) -> dict[str, Any]:
        spec = self.field_map.get(fact_key)
        history = _HISTORY.fullmatch(fact_key)
        if spec is None and history:
            # Not a box itself, but re-derived into one after each decision.
            kind, n = history.group(1), int(history.group(2))
            feeds = ("Part 1, Item 18 (previous address)" if kind == "prior_address" and n == 1 else
                     f"Part 7 (child {n})" if kind == "child" and n <= 2 else "Part 14 (additional information)")
            date = history.group(3) in ("date_from", "date_to", "dob")
            return {"type": "date" if date else "text", "on_form": True, "feeds": feeds}
        if spec is None:
            return {"type": "text", "on_form": False}
        kind = spec.get("type", "text") if isinstance(spec, dict) else "text"
        if kind == "yes_no":
            return {"type": "choice", "options": ["Yes", "No"], "on_form": True}
        if kind == "choice_by_value":
            return {"type": "choice", "options": list(spec["options"]), "on_form": True}
        if kind == "date":
            return {"type": "date", "on_form": True}
        if kind == "height_feet_inches":
            return {"type": "text", "placeholder": "5'3\"", "pattern": r"^[3-8]'(\d|1[01])\"$", "on_form": True}
        if kind == "digits_split":  # one box per digit
            return {"type": "text", "maxlen": len(self._fields(fact_key)), "digits": True, "on_form": True}
        limits = [self.limits[f] for f in self._fields(fact_key) if f in self.limits]
        # digits_only boxes (SSN, A-Number, phone) get the digits alone: "123-45-6789" fits a 9-character box
        return {"type": "text", "maxlen": min(limits) if limits else None, "on_form": True, **({"digits": True} if kind == "digits_only" else {})}


# Plain labels for the review screen, by the fact key's ending. The I-485's
# own wording stays available as the full label and the Part/Item reference.
_SHORT_BY_SUFFIX = [
    ("address_since", "Living here since"), ("unit_type", "Apt / Ste / Flr"), ("apt", "Apt / Ste / Flr number"),
    ("street", "Street"), ("city", "City"), ("state", "State"), ("zip", "ZIP code"), ("province", "Province"),
    ("postal_code", "Postal code"), ("country_of_birth", "Country of birth"), ("country", "Country"),
    ("date_from", "From"), ("date_to", "To"), ("occupation", "Occupation"), ("employer1_name", "Employer or school"),
    ("employer_name", "Employer or school"), ("dob", "Date of birth"), ("times_married", "Times married"),
    ("total_children", "Number of children"), ("height", "Height"), ("weight_lbs", "Weight (lb)"),
    ("hair_color", "Hair color"), ("eye_color", "Eye color"), ("marital_status", "Marital status"),
    ("ethnicity", "Ethnicity"), ("race", "Race"), ("sex", "Sex"),
    ("birth_family_name", "Family name at birth"), ("birth_given_name", "Given name at birth"),
    ("birth_middle_name", "Middle name at birth"), ("family_name", "Family name"), ("given_name", "Given name"),
    ("a_number", "A-Number"), ("in_military", "In the U.S. armed forces or Coast Guard?"),
    ("applying_with_you", "Applying with you?"), ("address_country", "Country"), ("birth_city", "City of birth"),
    ("marriage_date", "Date"), ("other_names", "Other names used"), ("prior_spouse", "Prior spouse (all boxes)"),
    ("foreign_address", "Province (foreign-address box)"), ("current_spouse", "Current spouse (all boxes)"),
    ("children", "Children (all boxes)"),
]
_OWNER = {"mother_": "Mother: ", "father_": "Father: ", "spouse_": "Spouse: ", "marriage_": "Marriage: ",
          "last_foreign_": "Last address outside the U.S.: ", "foreign_employer_": "Last job outside the U.S.: ",
          "prior_address_": "Previous address: "}
# Boxes whose own PDF tooltip is wrong (the Part 1 item 11 checkboxes are
# named and described as Part 2's "Special Programs"; the three text boxes
# beside them all say "25. D. Enter Other").
LABEL_OVERRIDES = {
    "applicant.last_arrival_manner": ("When I last arrived in the United States, I was: admitted, paroled, came in without admission or parole, or other",
                                      "Part 1, Item 11"),
    # Implementation note.
    "applicant.last_arrival_date": ("Date of last arrival", "Part 1, Item 10"),
    "applicant.last_arrival_city": ("City or town of last arrival", "Part 1, Item 10"),
    "applicant.last_arrival_state": ("State of last arrival", "Part 1, Item 10"),
    "applicant.last_arrival_admitted_as": ("When I last arrived: admitted as (class of admission)", "Part 1, Item 11.a"),
    "applicant.last_arrival_paroled_as": ("When I last arrived: paroled as", "Part 1, Item 11.b"),
    "applicant.last_arrival_other": ("When I last arrived: other (explanation)", "Part 1, Item 11.d"),
    # the name timeline's two decisions (src/name_events.py): not boxes themselves, they settle Part 1 items 1 and 2 on every form
    "applicant.name_current": ("The client's current legal name", "Part 1, Items 1 and 2"),
    "applicant.name_uscis_ok": ("The name this filing uses, where USCIS knows the client by another", "Part 1, Item 1"),
    "applicant.name_uscis_also": ("Also list USCIS's spelling as an other name used (optional)", "Part 1, Item 2"),
    # brief K6: the two empty boxes on "Which name is current?" when the marriage certificate prints no name after marriage
    "applicant.name_chosen_given": ("Or type the client's current name: given name(s)", "Part 1, Item 1"),
    "applicant.name_chosen_family": ("Or type the client's current name: family name(s)", "Part 1, Item 1"),
}
NAME_CARDS = {"names": "Which name is current?", "names_uscis": "USCIS knows the client by another name"}


def concise(text: str, limit: int = 110) -> str:
    """A card headline from the I-485's own wording: the question itself,
    without the section preamble, item numbers or parentheticals --
    "Have you EVER been arrested, cited, charged, or permitted to participate
    in a diversion program, or detained for..." rather than four lines. The
    full wording stays available on the card."""
    t = re.sub(r"\s+", " ", text or "").strip()
    parts = [x for x in re.split(r"(?<=[a-z)]{2}[.?])\s+(?=[A-Z0-9])", t) if x]
    parts = [x for x in parts if not x.upper().startswith("NOTE")] or parts  # "NOTE: The term children includes..." is not the question
    if not parts:
        return t
    question = next((x for x in parts if "?" in x), None)
    if question is None:
        tail = parts[-1]
        question = (parts[-2] + " " + tail) if len(parts) > 1 and len(tail) < 40 and re.match(r"(Enter|Select)\b", tail) else tail
    q = re.sub(r"I\s?-\s?4\s?8\s?5", "I-485", question)
    q = re.sub(r"^(Additional\s+)?(Part \d+\.\s*)?(Additional\s+)?(\d{1,2}\s?\.?\s?[A-Za-z]?\.0?\s*(?=[A-Z])\s*)+", "", q)
    q = re.sub(r"\b\d{1,2}\.\s+(Enter|Select)\s+", "", q)
    q = re.sub(r"^(Enter|Select)\s+", "", q)
    q = re.sub(r"\.?\s+(Enter|Select)\s+", ": ", q)
    if len(q) > 80:  # long asides only, and only when the line is long: "(lb)", "(Notice to Appear)" stay
        q = re.sub(r"\s*\([^)]{16,}\)", "", q)
    q = q.strip(" .")
    q = q[:1].upper() + q[1:]
    if len(q) > limit:
        q = q[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return q


def display_name(client_dir: Path, from_facts: str | None = None) -> str:
    """The name a case goes by on a screen: the client's name from the questionnaire or a document (from_facts, case_summary's), else the
    name the portal holds for the client, else "New case, MM/DD/YYYY" (the day the case was made): never the id the folder is named by."""
    # Only an explicit named-human correction overrides the case's name.
    catalog = _read(Path(client_dir) / "documents.json", {})
    subjects = (catalog.get("case_subjects") or {}) if isinstance(catalog, dict) else {}
    people = subjects.get("people", []) if subjects.get("case_id") == Path(client_dir).name else []
    named = [p for p in people if p.get("case_role") == "applicant" and p.get("active") is not False
             and p.get("history") and p.get("who") and p.get("role") in {"attorney", "paralegal"}
             and not str(p.get("label", "")).startswith("Client in case ")]
    if len(named) == 1:
        return named[0]["label"]
    if from_facts:
        return from_facts
    try:
        profile = _read(_portal_for(Path(client_dir)) / "clients" / Path(client_dir).name / "profile.json", {})
    except OSError:
        profile = {}
    if isinstance(profile, dict) and str(profile.get("name") or "").strip():
        return str(profile["name"]).strip()
    # the day the case was made: its earliest file, which reprocessing never moves (the meta file and the facts are rewritten every run)
    times = []
    for p in Path(client_dir).rglob("*"):
        try:
            if p.is_file():
                times.append(p.stat().st_mtime)
        except OSError:
            continue
    if not times and Path(client_dir).exists():
        times.append(Path(client_dir).stat().st_mtime)
    day = clock.us_date(datetime.fromtimestamp(min(times), clock.zone()).isoformat()) if times else ""
    return f"New case, {day}" if day else "New case"


def case_summary(graph: FactGraph) -> dict[str, Any]:
    """Who this case is, for the review screen's header."""
    def value(key):
        f = graph.get(key)
        return f.value if f is not None and f.status == "resolved" and f.value not in (None, "") else None

    name = " ".join(x for x in (value("applicant.given_name"), value("applicant.middle_name"), value("applicant.family_name")) if x)
    category = "Special Immigrant Juvenile (I-360)" if value("applicant.i360_receipt_number") or value("applicant.public_charge_exemption") == "SIJS" else None
    return {"name": name or None, "a_number": value("applicant.a_number"), "dob": value("applicant.dob"),
            "country": value("applicant.country_of_birth"), "category": category, "state": value("applicant.physical_state"),
            "i360_receipt": value("applicant.i360_receipt_number"),
            "entry": value("applicant.last_arrival_manner"), "entry_date": value("applicant.last_arrival_date") or value("applicant.i94_arrival_date") or value("applicant.last_arrival_date_self_reported")}


CONTEXT_LABELS = {
    "questionnaire.dual_citizenship": "Has citizenship of more than one country",
    "questionnaire.entered_via_border": "Says they came in across the border",
    "questionnaire.has_i94_or_parole": "Says they have an I-94 or parole",
    "questionnaire.someone_petitioned_for_applicant": "Says someone applied for a visa or green card for them",
    "questionnaire.other_immigration_applications": "Says they applied for other immigration processes (besides SIJS)",
    "questionnaire.blank.other_names": "Left 'other names used' blank",
    "questionnaire.a_number": "A-number written on the questionnaire",
    "applicant.last_arrival_date_self_reported": "Entry date as the client wrote it",
    "applicant.i94_arrival_date": "Date of last arrival (I-94)",
    "applicant.a_number": "A-number",
    "questionnaire.dob": "Date of birth as the client wrote it",
    "questionnaire.birth_place": "Place of birth as the client wrote it",
    "questionnaire.mother_name": "Mother's full name as the client wrote it",
    "questionnaire.mother_birth_name": "Mother's maiden name as the client wrote it",
    "questionnaire.father_name": "Father's full name as the client wrote it",
    "questionnaire.father_birth_name": "Father's name at birth as the client wrote it",
    "questionnaire.marriage_date": "Marriage date as the client wrote it",
    "questionnaire.marriage_place": "Marriage place as the client wrote it",
    "applicant.birth_certificate_name": "Name on the birth certificate",
    "applicant.name_current_typed": "Current legal name as the client wrote it",
    "applicant.name_birth_typed": "Name at birth as the client wrote it",
    "questionnaire.name_changed": "Says their name changed (marriage or otherwise)",
    "applicant.birth_cert.naturalidade": "Birth certificate: 'naturalidade'",
    "applicant.birth_cert.grandparents": "Birth certificate: grandparents",
    "applicant.birth_cert.mother_name": "Birth certificate: mother",
    "applicant.birth_cert.father_name": "Birth certificate: father",
    "applicant.birth_cert.parent_a_name": "Birth certificate: first parent listed",
    "applicant.birth_cert.parent_b_name": "Birth certificate: second parent listed",
    "applicant.birth_cert.parent_a_birthplace": "Birth certificate: first parent's birthplace",
    "applicant.birth_cert.parent_b_birthplace": "Birth certificate: second parent's birthplace",
    "applicant.marriage_cert_birthplace": "Marriage certificate: client's place of birth",
    "applicant.spouse_birth_city": "Spouse's city of birth",
    "applicant.mother_birth_city": "Mother's city of birth",
    "applicant.father_birth_city": "Father's city of birth",
    "applicant.birth_state": "State / province of birth",
}

# Cross-document checks that aren't "client vs document": their own titles.
CROSSCHECK_TITLES = {
    "applicant.birth_city": "City of birth: the sources disagree",
    "applicant.i94_number": "The client said no I-94, but one is in the folder",
    "applicant.total_children": "Number of children doesn't match the children listed",
    "applicant.last_foreign_date_to": "Last address outside the U.S.: dates don't fit the entry date",
    arrival.CITY: "Last arrival in the U.S.: the client's answer differs from the government paper",
    "applicant.last_foreign_city": "Last address outside the U.S.: check the city",
    "applicant.last_foreign_province": "Last address outside the U.S.: check the province",
    "applicant.last_foreign_country": "Last address outside the U.S.: check the country",
    "applicant.last_foreign_postal_code": "Last address outside the U.S.: check the postal code",
    "applicant.dob": "Date of birth: the client's answer differs from the documents",
    "applicant.marriage_cert_birthplace": "Place of birth on the marriage certificate",
    "applicant.birth_certificate_name": "Name on the birth certificate differs from USCIS documents",
    "applicant.marriage_date": "Marriage date: the client's answer differs from the certificate",
    "applicant.mother_family_name": "Mother's name on the birth certificate",
    "applicant.father_family_name": "Father's name on the birth certificate",
    "applicant.mother_birth_family_name": "Mother's name at birth: same as the current name",
    "applicant.father_birth_family_name": "Father's name at birth: same as the current name",
}

# All checks on the last foreign address are one card with every box of it.
LAST_FOREIGN = "applicant.last_foreign_"

# Check-tab cards that gather one person's boxes, whatever source they came from.
PERSON_CARDS = [("applicant.mother_", "Mother (Parent 1)"), ("applicant.father_", "Father (Parent 2)"),
                ("applicant.spouse_", "Current spouse"), ("applicant.marriage_", "Current marriage"),
                (("applicant.prior_address_", "applicant.lived_at_address_5yrs"), "Previous address (Part 1, Item 18)"),
                ("applicant.p14_", "Part 14 · additional information"), ("applicant.child1_", "Child 1"), ("applicant.child2_", "Child 2")]

# A questionnaire answer that feeds differently named form facts, so its scan
# crop shows on their card.
EVIDENCE_ALIASES = {
    **{f"questionnaire.prior_address1_{part}": [f"applicant.prior_address_{part}"]
       for part in ("street", "apt", "city", "state", "zip", "province", "postal_code", "country", "date_from")},
    "questionnaire.mother_name": ["applicant.mother_given_name", "applicant.mother_family_name"],
    "questionnaire.mother_birth_name": ["applicant.mother_birth_given_name", "applicant.mother_birth_family_name"],
    "questionnaire.father_name": ["applicant.father_given_name", "applicant.father_family_name"],
    "questionnaire.father_birth_name": ["applicant.father_birth_given_name", "applicant.father_birth_family_name"],
    "questionnaire.birth_place": ["applicant.birth_city"],
    "questionnaire.dob": ["applicant.dob"],
    "questionnaire.marriage_date": ["applicant.marriage_date"],
    "questionnaire.marriage_place": ["applicant.marriage_city", "applicant.marriage_country"],
}
CONTEXT_PREFIXES = ("questionnaire.", "applicant.birth_cert.")

# Questions whose answers belong on one card (address + address history,
# a parent's date and country of birth, entry date and place).
CARD_WITH = {"current_address_history": "physical_address", "mother_birth_country": "mother_dob",
             "father_birth_country": "father_dob", "last_entry_place": "last_entry_date"}

# Card titles for questionnaire answers, by question/field id.
QUESTION_TITLES = {
    "physical_address": "Current home address", "current_address_history": "Current address (address history)",
    "last_foreign_address": "Last address outside the U.S.", "current_employer": "Current job or school",
    "last_foreign_employer": "Last job or school outside the U.S.", "mother_dob": "Mother",
    "mother_birth_country": "Mother", "father_dob": "Father", "father_birth_country": "Father",
    "times_married": "Marriages", "total_children": "Children", "height": "Height", "weight": "Weight",
    "hair_color": "Hair color", "last_entry_date": "Last entry into the U.S.", "last_entry_place": "Last entry into the U.S.",
    "other_names": "Other names used", "applied_immigrant_visa_abroad": "Immigrant visa applied for abroad",
    "sex": "Sex", "marital_status": "Marital status", "ethnicity": "Ethnicity", "race": "Race",
    "birth_place": "Place of birth", "mother_name": "Mother's full name", "mother_birth_name": "Mother's maiden name",
    "father_name": "Father's full name", "father_birth_name": "Father's name at birth", "marriage_date": "Date of marriage",
    "marriage_place": "Place of marriage", "applicant_dob": "Date of birth", "eye_color": "Eye color",
}


_HISTORY = re.compile(r"questionnaire\.(prior_address|prior_employer|child)(\d)_(\w+)$")
_HISTORY_NAMES = {"prior_address": "Previous address", "prior_employer": "Earlier job or school", "child": "Child"}
_PART_WORDS = {"street": "Street", "apt": "Apt number", "city": "City", "state": "State", "zip": "ZIP code", "province": "Province",
               "postal_code": "Postal code", "country": "Country", "date_from": "From", "date_to": "To", "employer": "Employer or school",
               "occupation": "Occupation", "name": "Full name", "a_number": "A-Number", "dob": "Date of birth"}


def short_label(key: str, full: str) -> str:
    if key in CONTEXT_LABELS:
        return CONTEXT_LABELS[key]
    block = re.fullmatch(r"applicant\.p14_block(\d+)_(page|part|item|text)", key)
    if block:
        return f"Entry {block.group(1)}: " + {"page": "refers to page", "part": "Part", "item": "Item", "text": "text in Part 14"}[block.group(2)]
    history = _HISTORY.fullmatch(key)
    if history:
        return f"{_HISTORY_NAMES[history.group(1)]} {history.group(2)}: {_PART_WORDS.get(history.group(3), history.group(3))}"
    name = key.rsplit(".", 1)[-1]
    owner = next((v for k, v in _OWNER.items() if name.startswith(k)), "")
    # longest ending first: "ethnicity" must not match "city"
    for suffix, label in sorted(_SHORT_BY_SUFFIX, key=lambda sl: -len(sl[0])):
        if name.endswith(suffix):
            return owner + label
    return full  # Part 9 etc.: the question itself is the clearest label


def i485_ref(full: str) -> str:
    """"Part 1. ... 18. Enter Street..." -> "Part 1, Item 18"."""
    import re as _re

    part = _re.search(r"Part (\d+)\.?", full)
    item = _re.search(r"(?:^|\s)(\d{1,2}(?:\s?[A-Z])?)\.(?:\s|$|(?=[A-Z][a-z]))", full[part.end():] if part else full)
    return ", ".join(x for x in (part and f"Part {part.group(1)}", item and f"Item {item.group(1).replace(' ', '')}") if x)


# Boxes whose printed tooltip carries a stray item number. The I-485's I-94 number box (P1Line12_I94) says "26. A." and stands in Item 12:
# (the tooltip's reference, the item it really is). Only these are overridden: everywhere else the tooltip's printed item number is the
# form's own and wins over the box's name (names and printed numbers differ legitimately: Part 9 items, the G-28's Part 3).
STRAY_REFS = {"P1Line12_I94": ("Part 1, Item 26", "Part 1, Item 12")}


def ref_by_name(ref: str, field_name: str) -> str:
    """The Part and Item of a box: the tooltip's, except for a box in STRAY_REFS (buyer visit 3: "Item 26 between two Item 12 rows")."""
    for stray, (shown, real) in STRAY_REFS.items():
        if stray in (field_name or "") and ref == shown:
            return real
    return ref


def _source_reader(manifest: dict | None) -> dict:
    """Summarize the recorded read, never substitute current configuration."""
    import reader_manifest
    from document_instances import digest
    if not isinstance(manifest, dict):
        return {"state": "unrecorded", "configuration_digest": None,
                "normalization_digest": None, "release_revision": None,
                "upstream_identity": "unavailable"}
    upstream = manifest.get("upstream")
    if upstream is not None and not isinstance(upstream, dict):
        return {"state": "unavailable", "configuration_digest": reader_manifest.fingerprint(manifest),
                "normalization_digest": None, "release_revision": None,
                "upstream_identity": "unavailable", "reason": "Recorded reader metadata is malformed; identity unavailable."}
    return {"state": "recorded", "configuration_digest": reader_manifest.fingerprint(manifest),
            "normalization_digest": digest(manifest.get("normalization_assets", {})),
            "release_revision": manifest.get("declared_reader_release_revision"),
            "capture_scope": manifest.get("capture_scope"),
            "upstream_identity": (upstream or {}).get("execution_identity", "unavailable")}


def _fact_view(graph: FactGraph, key: str, catalog: Catalog) -> dict[str, Any]:
    fact = graph.get(key)
    full = catalog.label(key)
    ref = "Part 14" if key.startswith("applicant.p14_") else catalog.ref(key) or i485_ref(full)
    view = {"key": key, "label": full, "short": short_label(key, full), "ref": ref, "input": catalog.input(key)}
    if fact is None:
        return view | {"value": None, "status": "absent", "tier": None, "sources": []}
    rule = rule_info(fact.derived_by) if fact.derived_by else None
    if any(s.doc_id == "portal questionnaire" for s in fact.sources):
        from portal.bank import all_questions, load_bank
        for q in all_questions(load_bank()).values():
            part = next((p for p, f in (q.get("facts") or {}).items() if f == key), None)
            if q.get("fact") != key and part is None:
                continue
            field = next((f for f in q.get("fields", []) if f["id"] == part), {})
            label = (q.get("label") or {}).get("en") or q["id"].replace("_", " ")
            field_label = (field.get("label") or {}).get("en") or _PART_WORDS.get(part, part)
            view["portal_question"] = {"id": q["id"], "label": label, "field": field_label, "ref": q.get("i485")}
            view["short"] = label + (f": {field_label}" if field_label else "")
            break
    return view | {
        "value": fact.value,
        "status": fact.status,
        "tier": fact.tier,
        "derived_by": fact.derived_by,
        "why": catalog.policy_why.get(fact.derived_by or "", "") or (rule["plain_text"] if rule else ""),
        "rule_name": rule_name(fact.derived_by) if fact.derived_by and not fact.derived_by.startswith("POLICY:") else None,
        # the rule's plain text, source and the attorney's approval for every case (src/rules/approval.py)
        "rule": rule,
        "sources": [{"doc": s.doc_id, "type": s.doc_type, "raw": s.raw_value, "value": s.normalized_value,
                     "page": s.page, "instance_id": s.instance_id, "subject_role": s.subject_role,
                     "extracted_at": s.extracted_at, "evidence_version": s.evidence_version,
                     "reading_issues": s.reading_issues,
                     "reader": _source_reader(s.read_manifest)} for s in fact.sources],
        "review": asdict(fact.review) if fact.review else None,
    }


def _evidence_index(evidence: dict[str, dict[str, dict]]) -> tuple[dict[str, list], dict[str, tuple]]:
    by_fact: dict[str, list] = {}
    by_question: dict[str, tuple] = {}
    for doc, entries in evidence.items():
        for qid, ev in entries.items():
            by_question[qid] = (doc, ev)
            keys = [ev["fact_key"]] if ev.get("kind") == "choice" else list(ev.get("facts", {}).values())
            keys += [k[: -len("_apt")] + "_unit_type" for k in keys if k.endswith("_apt")]  # derived from the apt number
            keys += [alias for k in keys for alias in EVIDENCE_ALIASES.get(k, [])]
            for key in keys:
                by_fact.setdefault(key, []).append((doc, qid, ev))
    return by_fact, by_question


def _ev_view(doc: str, qid: str, ev: dict) -> dict[str, Any]:
    return {"doc": doc, "question": qid} | {k: ev.get(k) for k in ("kind", "input_kind", "ambiguous", "amended", "page", "box", "status", "reason", "reads", "ink", "describe", "options", "facts", "typed", "vision", "check", "suggest")}


def source_prerequisites(client_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Explain the same raw-source prerequisites enforced by prepare_decision.

    A valid new edge does not waive an unresolved sibling supplying the same
    field. Names are returned only within the already authorized case view.
    """
    import document_instances
    import subject_attribution
    path = client_dir / "fact_graph_raw.json"
    if not path.exists():
        path = client_dir / "fact_graph.json"
    if not path.exists():
        return {}
    graph = FactGraph.load(path)
    held = document_instances.held_aliases(client_dir)
    reasons: dict[str, set[tuple[str, str, str]]] = {}
    for key, fact in graph.all_facts().items():
        for source in fact.sources:
            filename = source.doc_id.split("#p", 1)[0]
            if source.doc_id in held or filename in held:
                reasons.setdefault(key, set()).add((filename, "boundary", "Review this original's identity and page boundaries in Documents."))
    for row in subject_attribution.views(client_dir, graph):
        for fact in row["facts"]:
            if fact["state"] == "pending":
                reasons.setdefault(fact["key"], set()).add((row["file"], "subject", fact["reason"]))
    while True:
        before = {key: set(values) for key, values in reasons.items()}
        for key, fact in graph.all_facts().items():
            parents = set(fact.derived_from) | {k for s in fact.sources for k in s.from_facts}
            inherited = {entry for parent in parents for entry in reasons.get(parent, set())}
            if inherited:
                reasons.setdefault(key, set()).update(inherited)
        if before == reasons:
            break
    return {key: [{"file": file, "kind": kind, "reason": reason} for file, kind, reason in sorted(values)]
            for key, values in reasons.items()}


@read_scope.scoped
def build_items(client_dir: Path, field_map: dict, template_path: str | Path, catalog: Catalog, assume: dict[str, Any] | None = None,
                pending: bool = True) -> dict[str, Any]:
    """The review items. pending: also say how many more items settling the open answers would add (pending_items), so the count
    of open items does not rise as they are settled; assume (what pending_items passes) is an answer taken as settled for this reading."""
    graph, flags = current_flags(client_dir, field_map, template_path, assume)
    decisions = load_decisions(client_dir)
    by_fact, by_question = _evidence_index(_read(client_dir / "evidence.json", {}))

    items: dict[str, dict[str, Any]] = {}
    asked_by_reading = {
        k for f in flags if f.kind == "unread" and not _answered_by_documents(f, by_question, graph)
        for k in _form_keys(list((by_question.get(f.fact_key.split(".", 1)[1], (None, {}))[1] or {}).get("facts", {}).values()))
    }
    for flag in flags:
        if flag.level == "informational":
            continue
        if flag.kind == "missing" and flag.fact_key in asked_by_reading:
            continue  # the unreadable-line card asks the same thing, with the scan
        if flag.kind == "unread" and _answered_by_documents(flag, by_question, graph):
            continue
        if flag.kind == "unread" and re.fullmatch(r"questionnaire\.(prior_address|prior_employer|child)_\d", flag.fact_key) \
                and (by_question.get(flag.fact_key.split(".", 1)[1], (None, {}))[1] or {}).get("status") == "not_found":
            continue  # an optional history line the scan doesn't show clearly (the prior-address need is asked separately)
        iid = item_id(flag)
        item = items.get(iid)
        if item is None:
            item = items[iid] = _new_item(iid, flag, graph, catalog, by_fact, by_question)
        message = plain_message(flag, item)
        if message and message not in item["messages"]:
            item["messages"].append(message)
        if item.get("rule_note"):  # after what the card says, the rule that said it (ARRIVAL-01)
            if item["rule_note"] not in item["messages"]:
                item["messages"].append(item["rule_note"])
        if flag.level == "blocking":
            item["level"] = "blocking"

    critical_context = __import__('critical_review').context(client_dir, decisions)
    prerequisites = source_prerequisites(client_dir)
    for item in items.values():
        item["group"] = _group(item)
        required = [{"key": f["key"], "label": f["short"], **reason}
                    for f in item["facts"] for reason in prerequisites.get(f["key"], [])]
        if required:
            item["source_prerequisites"] = required
        selected = {f["key"]: critical_context[f["key"]] for f in item["facts"] if f["key"] in critical_context}
        if selected:
            item["evidence_fingerprints"] = {key: c["fingerprint"] for key, c in selected.items()}
            item["source_review"] = {"basis": "manual_retained_source_review", "model_release_approval": False,
                                     "reasons": sorted({r for c in selected.values() for r in c["reasons"]})}
    done = []
    for iid, d in sorted(decisions.items(), key=lambda kv: clock.key(kv[1]["at"]), reverse=True):
        facts = [_fact_view(graph, k, catalog) for k in d["item"]["facts"]]
        done.append({"id": iid, "decision": {k: v for k, v in d.items() if k != "history"}, "title": _done_title(d["item"], facts),
                     "group": d["item"]["group"], "facts": facts, "history": _history(d)})
    # undone and not decided again: the Decision log still shows who decided what, and who reopened it
    reopened = []
    for iid, d in sorted(((i, d) for i, d in load_decision_log(client_dir).items() if d.get("undone")),
                        key=lambda kv: kv[1]["undone"].get("at") or "", reverse=True):
        facts = [_fact_view(graph, k, catalog) for k in d["item"]["facts"]]
        reopened.append({"id": iid, "title": _done_title(d["item"], facts), "headline": concise(_done_title(d["item"], facts)),
                         "facts": facts, "history": _history(d), "undone": d["undone"]})
    order = {"blocking": 0, "needs_input": 1, "attorney": 2, "questionnaire": 3, "other": 5}
    open_items = sorted(items.values(), key=lambda i: (order.get(i["group"], 4), i["group"], i["title"]))
    meta = _read(client_dir / "meta.json", {})
    context = [
        {"key": k, "label": CONTEXT_LABELS.get(k, k), "value": f.value}
        for k, f in graph.all_facts().items()
        if (k.startswith(CONTEXT_PREFIXES) or (k in CONTEXT_LABELS and k not in catalog.field_map)) and f.status == "resolved"
        and not k.startswith("questionnaire.blank.")
    ]
    cards = build_cards(open_items)
    order = {doc: list(questions) for doc, questions in _read(client_dir / "evidence.json", {}).items()}
    found = {(doc, qid): ev.get("page") for doc, questions in _read(client_dir / "evidence.json", {}).items()
             for qid, ev in questions.items() if ev.get("page") is not None}
    for card in cards:
        card["headline"] = concise(card["title"])
        typed = [f for f in card["facts"] if any(s.get("doc") == OFFICE_DOC_ID for s in f.get("sources") or [])]
        if typed:  # office_answered: the box holds the client's typed answer; the card says so before anything else
            card["filled_from_client"] = True
            card["messages"].insert(0, "Filled from the client's answer to the office's question: check it against the documents, then confirm "
                                       "or correct it." if len(typed) == len(card["facts"]) else
                                    "Filled from the client's answer to the office's question: " + ", ".join(f.get("short") or f["label"] for f in typed)
                                    + ". Check it, then confirm or correct it.")
        only = card["facts"][0] if len(card["facts"]) == 1 else None
        if only and not card.get("timeline") and (card["id"].startswith("q:") or ": " not in (only.get("short") or "")):
            # the question itself, not the questionnaire section or person card it sits in
            # ("Have you resided at your current address for at least 5 years?", not "Previous address")
            card["headline"] = concise(only["label"])
        if card["id"] == "p:Part 14. Additional information":
            card["headline"] = "Part 14 · answers that don't fit on the form"
            card["messages"].insert(0, "Why this is here: the I-485 has room for only one job, one previous address, one earlier marriage and "
                                       "so on. The rest of the client's answers go in Part 14 (additional information), each pointing to the "
                                       "item it continues. They were written from the questionnaire line shown on the left.")
        for ev in card["evidence"]:
            if ev.get("page") is None and ev.get("doc") in order and ev.get("question") in order[ev["doc"]]:
                # Not found on the scan: show the page between the questions around it that were found.
                questions = order[ev["doc"]]
                i = questions.index(ev["question"])
                near = sorted(((abs(j - i), found[(ev["doc"], q)]) for j, q in enumerate(questions) if (ev["doc"], q) in found))
                if near:
                    ev["page"], ev["page_guess"] = near[0][1], True
        prefix = next((p for p, title in PERSON_CARDS if card["id"] == f"p:{title}"), None)
        if prefix:
            # What's already on the form for this person, so the boxes to
            # confirm are seen in context ("APT" of which address?).
            shown = {f["key"] for f in card["facts"]}
            card["known"] = [
                v for k in sorted(graph.all_facts(), key=lambda k: _field_rank({"key": k}))
                if k.startswith(prefix) and k not in shown and k in catalog.field_map
                and (v := _fact_view(graph, k, catalog))["status"] == "resolved" and v["value"] not in (None, "")
            ]
            if any("copied from" in (src.get("raw") or "") for f in card["facts"] for src in f.get("sources", [])):
                card["messages"].insert(0, "These boxes were copied from the client's own address, because the marriage "
                                           "certificate shows the spouse living at the same address. Confirm the spouse still lives there.")
    for d in done:
        d["headline"] = concise(d["title"])
    out = {"client_id": meta.get("client_id", client_dir.name), "open": open_items, "cards": cards,
           "done": done, "reopened": reopened, "meta": meta, "context": context, "summary": (lambda s: s | {"name": display_name(client_dir, s["name"])})(case_summary(graph))}
    if pending and assume is None:
        out["pending"] = pending_items(client_dir, field_map, template_path, catalog, graph, {i["id"] for i in open_items}, meta)
        headline = {f["key"]: c["headline"] for c in cards for f in c["facts"]}  # the open card each one waits on, by its own headline
        out["pending"]["because"] = [headline.get(k) or concise(catalog.label(k)) for k in out["pending"].pop("waiting_on")]
    return out


MAX_SIMULATIONS = 6  # the most "what if this were settled" readings one build makes


def pending_items(client_dir: Path, field_map: dict, template_path: str | Path, catalog: Catalog, graph: FactGraph,
                  open_ids: set[str], meta: dict[str, Any]) -> dict[str, Any]:
    """The items that settling an open answer will add, counted from the start so the open total does not rise as the answers
    are settled. A firm policy or a rule that waits on an answer two documents disagree about (the SSN card against what the client
    typed: the policy for a Social Security card already issued fills three more boxes once the number is settled) is asked what it
    would add: the case is read again with each source's value taken as the answer, on a copy that is never saved, and the items
    every one of those readings adds are pending (an item that depends on which source wins is not counted).
    -> {"count", "items": [{"id", "title"}], "waiting_on": [the open answers' fact keys]}; nothing when there is nothing to wait for."""
    nothing: dict[str, Any] = {"count": 0, "items": [], "waiting_on": []}
    try:
        if "derivation" not in meta or not (client_dir / "fact_graph_raw.json").exists():
            return nothing
        from rules.policy import conditions_hold

        _rules, policies = _derivation(meta["derivation"])
        today = clock.today()
        waiting: dict[str, list[Any]] = {}
        for policy in policies or []:
            if conditions_hold(graph, policy, today):
                continue
            inputs = policy.get("when_present", []) + list(policy.get("when_values", {})) + (["applicant.dob"] if "max_age" in policy else [])
            for key in inputs:
                fact = graph.get(key)
                if fact is not None and fact.status == "conflict" and fact.review is None:
                    waiting.setdefault(key, list(dict.fromkeys(s.normalized_value for s in fact.sources if s.doc_id != "paralegal_review")))
        added: dict[str, dict[str, Any]] = {}
        because = []
        runs = 0
        for key, values in waiting.items():
            ids: set[str] | None = None
            titles: dict[str, str] = {}
            for value in values:
                runs += 1
                if runs > MAX_SIMULATIONS:
                    return nothing | {"count": len(added), "items": list(added.values()), "waiting_on": because}
                after = build_items(client_dir, field_map, template_path, catalog, assume={key: value}, pending=False)
                new = {i["id"]: i for i in after["open"] if i["id"] not in open_ids}
                titles |= {iid: concise(", ".join(dict.fromkeys(f["short"] for f in i["facts"])) or i["title"], 80) for iid, i in new.items()}
                ids = set(new) if ids is None else ids & set(new)
            if ids:
                added |= {iid: {"id": iid, "title": titles[iid]} for iid in sorted(ids)}
                because.append(key)
        return {"count": len(added), "items": list(added.values()), "waiting_on": because}
    except Exception:  # noqa: BLE001 -- a count that cannot be worked out is left out, never a failed screen
        return nothing


def _done_title(item: dict[str, Any], facts: list[dict[str, Any]]) -> str:
    """Titles are rebuilt from today's labels, not the ones stored with an
    old decision (which could be a raw fact key)."""
    if item.get("kind") in NAME_CARDS:
        return NAME_CARDS[item["kind"]]
    if item.get("kind") == "absence":
        import absence

        return absence.title(item["id"])
    if item.get("kind") in ("alert", "blank_template", "unsupported_language", "crosscheck", "document_person", "part14") or not facts:
        return re.sub(r"\s*\(not on the I-485(?: --|:) context for the reviewer\)", "", item.get("title", ""))
    shorts = list(dict.fromkeys(f["short"] for f in facts))
    return ", ".join(shorts[:3]) + (" ..." if len(shorts) > 3 else "")


def _tab(item: dict[str, Any]) -> str:
    if item["group"] in ("blocking", "needs_input"):
        return "fix"
    if item["group"] == "attorney" or item["group"].startswith("rule:"):
        return "attorney"
    return "check"


# Reading order of fields within a card: as written on an address / job line.
_FIELD_ORDER = ["name", "occupation", "street", "unit_type", "apt", "city", "state", "province", "zip", "postal_code",
                "country", "address_since", "date_from", "date_to", "dob", "country_of_birth"]


def _field_rank(fact: dict[str, Any]) -> int:
    block = re.fullmatch(r"applicant\.p14_block(\d+)_(page|part|item|text)", fact["key"])
    if block:
        return 100 + 4 * int(block.group(1)) + ("page", "part", "item", "text").index(block.group(2))
    name = fact["key"].rsplit(".", 1)[-1]
    matches = [i for i, suffix in enumerate(_FIELD_ORDER) if name.endswith(suffix)]
    return matches[-1] if matches else len(_FIELD_ORDER)


PORTAL_PARTS = {1: "Part 1, About you", 2: "Part 2, Filing category", 4: "Part 4, Additional information", 5: "Part 5, Parents",
                6: "Part 6, Marital history", 7: "Part 7, Children", 8: "Part 8, Your description", 9: "Part 9, Eligibility",
                10: "Part 10, Contact information"}


def build_cards(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the screen shows: one card per ANSWER, not per fact. Client
    answers that came from the same questionnaire question (street, apt,
    city, state, ZIP, since...) share a card with one crop and one Confirm;
    everything else is one card per item."""
    cards: dict[str, dict[str, Any]] = {}
    for item in items:
        tab = _tab(item)
        qid = item["evidence"][0]["question"] if item["evidence"] else None
        question = CARD_WITH.get(qid, qid) if item["kind"] == "fact" and tab == "check" else None
        person = next((title for prefix, title in PERSON_CARDS if any(f["key"].startswith(prefix) for f in item["facts"])), None) \
            if item["kind"] == "fact" and tab == "check" else None
        address = item["kind"] == "crosscheck" and item["facts"] and all(f["key"].startswith(LAST_FOREIGN) for f in item["facts"])
        # Answers typed into the portal have no scan to compare against: one card per I-485
        # Part, read over and confirmed together (a portal client had 133 one-answer cards).
        portal = None
        if item["kind"] == "fact" and tab == "check" and not person and item["facts"] and all(
                any(s.get("doc") in ("portal questionnaire", "office question") for s in f.get("sources", [])) for f in item["facts"]):
            part = re.search(r"Part (\d+)", item["facts"][0].get("ref") or "")
            portal = int(part.group(1)) if part else 99
        portal_question = (item["facts"][0].get("portal_question") or {}) if item["facts"] else {}
        portal_key = f"portal:question:{portal_question['id']}" if portal == 99 and portal_question else f"portal:fact:{item['facts'][0]['key']}" if portal == 99 else f"portal:{portal}"
        key = ("x:last_foreign" if address else f"p:{person}" if person else portal_key if portal is not None
               else f"q:{question}" if question else item["id"])
        card = cards.get(key)
        if card is None:
            history = re.fullmatch(r"(prior_address|prior_employer|child)_(\d)", qid or "")
            if address:
                title = "Last address outside the U.S.: needs correcting"
            elif portal is not None:
                title = (portal_question.get("label") or item["facts"][0].get("short") or item["title"]) if portal == 99 else f"{PORTAL_PARTS.get(portal, 'Questionnaire answers')}: the client's answers from the portal"
            elif person:
                title = person
            elif history:
                n = int(history.group(2))
                title = {"prior_address": f"Previous address {n} (address history)", "prior_employer": f"Earlier job or school {n}",
                         "child": f"Child {n}"}[history.group(1)]
            else:
                title = QUESTION_TITLES.get(question or qid, item["title"]) if item["kind"] in ("fact", "unread") and qid else item["title"]
            title = re.sub(r"^Part \d+\.\s+((Criminal Acts and Violations|Additional Information About You|Security and Related)\.\s+)?", "", title)
            title = title[:1].upper() + title[1:]
            card = cards[key] = {
                "id": key, "tab": tab, "group": item["group"], "title": title, "level": item["level"],
                "kind": item["kind"], "item_ids": [], "facts": [], "evidence": [], "messages": [], "docs": [],
                "actions": list(item["actions"]), "context": [], "qa": [],
            }
        card["item_ids"].append(item["id"])
        if item.get("comparison"):
            card["comparison"] = item["comparison"]
        if item.get("source_prerequisites"):
            required = card.setdefault("source_prerequisites", [])
            required.extend(r for r in item["source_prerequisites"] if r not in required)
        if item.get("evidence_fingerprints"):
            card.setdefault("evidence_fingerprints", {}).update(item["evidence_fingerprints"])
            review = card.setdefault("source_review", {"basis": "manual_retained_source_review", "reasons": []})
            review["reasons"] = sorted(set(review["reasons"]) | set(item.get("source_review", {}).get("reasons", [])))
        for f in item["facts"]:
            if all(f["key"] != g["key"] for g in card["facts"]):
                card["facts"].append(f | {"item_id": item["id"]})
        for e in item["evidence"]:
            if all((e["doc"], e.get("page"), e.get("box")) != (g["doc"], g.get("page"), g.get("box")) for g in card["evidence"]):
                card["evidence"].append(e)
        card["messages"] += [m for m in item["messages"] if not SIGN_OFF.search(m)]
        card["context"] += [f for f in item.get("context", []) if all(f["key"] != g["key"] for g in card["context"])]
        card["qa"] += [r for r in item.get("qa", []) if r not in card["qa"]]
        card["docs"] = sorted(set(card["docs"]) | set(item["docs"]))
        card["actions"] = [a for a in card["actions"] if a in item["actions"]]
        if item.get("timeline"):  # the name cards (src/name_events.py): the events side by side
            card["timeline"] = item["timeline"]
        if item["level"] == "blocking":
            card["level"] = "blocking"
    for card in cards.values():
        card["facts"].sort(key=_field_rank)
    order = {"fix": 0, "check": 1, "attorney": 2}
    return sorted(cards.values(), key=lambda c: (order[c["tab"]], c["level"] != "blocking", c["group"], c["title"]))


# Answers computed from another answer: show the scan of that one.
DERIVED_FROM = {"applicant.lived_at_address_5yrs": ["applicant.physical_address_since"],
                # Part 1, item 11 and Part 9, item 73: from how the client said they entered (cases processed before Source.from_facts)
                "applicant.last_arrival_manner": ["questionnaire.entered_via_border", "questionnaire.entry_how", "applicant.last_arrival_date_self_reported"],
                "applicant.part9.pt9line75": ["questionnaire.entered_via_border", "questionnaire.entry_how"]}


def _evidence_keys(graph: FactGraph, key: str, by_fact: dict) -> list[str]:
    """The fact keys whose scans explain this one: itself, what it was
    derived from, and -- for a Part 14 box -- the questionnaire line it
    was built from ("composed from questionnaire.prior_employer3")."""
    keys = [key] + DERIVED_FROM.get(key, [])
    fact = graph.get(key)
    for source in (fact.sources if fact is not None else []):
        keys += [k for k in getattr(source, "from_facts", None) or [] if k not in keys]  # what it was worked out from
        line = re.match(r"composed from (questionnaire\.[a-z_]+\d+)$", str(source.raw_value or ""))
        if line:
            keys += [k for k in by_fact if k.startswith(line.group(1) + "_")]
    return keys


def _new_item(iid: str, flag: Flag, graph: FactGraph, catalog: Catalog, by_fact: dict, by_question: dict) -> dict[str, Any]:
    kind = flag.kind
    keys = [flag.fact_key]
    evidence: list[dict] = []
    if kind == "unread":
        qid = flag.fact_key.split(".", 1)[1]
        doc, ev = by_question.get(qid, (None, None))
        if ev is not None:
            evidence.append(_ev_view(doc, qid, ev))
            keys = [ev["fact_key"]] if ev.get("kind") == "choice" else list(dict.fromkeys(ev.get("facts", {}).values()))
            keys = _form_keys(keys)
    elif kind == "crosscheck" and flag.fact_key.startswith(LAST_FOREIGN):
        keys = [LAST_FOREIGN + part for part in ("street", "city", "province", "postal_code", "country", "date_from", "date_to")]
    elif kind == "crosscheck" and flag.fact_key == arrival.CITY:
        keys = arrival.card_keys(graph)  # the place, the date and the way of arriving: the client's answer against a DHS paper (src/arrival.py)
    elif kind == "crosscheck":
        partner = next((a for a, b in __import__("batch").CROSS_CHECKS if b == flag.fact_key), None)
        keys = [flag.fact_key] + ([partner] if partner else [])
    comparison = None
    if kind == "crosscheck" and flag.fact_key == "applicant.marriage_cert_birthplace":
        from review.filing_impact import birthplace_comparison, CITY
        comparison = birthplace_comparison(graph, catalog)
        if comparison and CITY not in keys:
            keys.append(CITY)  # inspect the actual filed city alongside the printed reference
    for key in keys:
        for source_key in _evidence_keys(graph, key, by_fact):
            for doc, qid, ev in by_fact.get(source_key, []):
                view = _ev_view(doc, qid, ev)
                if view not in evidence:
                    evidence.append(view)

    if kind == "crosscheck" and flag.fact_key == arrival.CITY:  # each DHS paper opens at the page that states the arrival
        evidence += [{"doc": p["doc"], "question": "arrival", "kind": "text", "page": p["page"], "describe": p["name"], "raw": p["raw"]}
                     for p in arrival.pages(graph)]
    facts = [_fact_view(graph, k, catalog) for k in keys] if kind not in ("alert", "blank_template", "unsupported_language", "document_boundary", "document_subject") else []
    if comparison:
        for fact in facts:
            if fact["key"] == "applicant.marriage_cert_birthplace":
                fact["comparison_readonly"] = True  # preserve what the paper says
            elif fact["key"] == "applicant.birth_city":
                if comparison["kind"] != "disagreement":
                    fact["comparison_readonly"] = True  # context only; Ack cannot select or approve this city
                elif comparison["kind"] == "disagreement":
                    fact["input"] = fact["input"] | {"alternatives": comparison["alternatives"],
                                                     "alternative_labels": comparison["alternative_labels"]}
    if kind in NAME_CARDS:
        return _name_item(iid, flag, graph, facts, catalog)
    if kind == "unread":
        _suggest_from_reads(facts, evidence)
        _suggest_names(facts, evidence)
        _suggest_from_documents(facts, graph)
    ambiguity = []
    for e in evidence:
        for sub, (used, other) in (e.get("ambiguous") or {}).items():
            fact = next((f for f in facts if f["key"] == (e.get("facts") or {}).get(sub)), None)
            if fact is not None and fact.get("value") == used:
                fact["alt"] = other
                ambiguity.append(f"{fact['short']}: the client wrote this date in a way that reads two ways. "
                                 f"{used[5:7]}/{used[8:]}/{used[:4]} or {other[5:7]}/{other[8:]}/{other[:4]}, and wrote dates "
                                 "both day-first and month-first on this form. Pick the right one (click the other reading to use it).")
    ambiguity += _apply_amendments(facts, evidence)
    if kind == "crosscheck" and flag.fact_key.startswith(LAST_FOREIGN):
        _foreign_fixes({f["key"]: f for f in facts}, graph)
    port = arrival.reading(graph) if kind == "crosscheck" and flag.fact_key == arrival.CITY else None
    if port:  # the notice's city read against CBP's ports (brief K6): the product's reading in the empty boxes, for a person to confirm or correct
        for f in facts:
            if f["key"] in (arrival.CITY, arrival.STATE) and f.get("value") in (None, "") and not f.get("review"):
                # a printed state that contradicts CBP's list: no state is suggested, the card names both and a person types it
                f["suggest"] = port["city"] if f["key"] == arrival.CITY else (None if port.get("mismatch") else port["state"])
    if kind == "missing":
        for f in facts:
            if re.search(r"_birth_(given|family|middle)_name$", f["key"]):
                f["suggest"] = "NOT APPLICABLE"  # the usual answer: the name never changed
    if kind in ("document_boundary", "document_subject"):
        actions = []  # only the version-bound Documents control may settle it
    elif kind in ("alert", "blank_template", "unsupported_language", "crosscheck"):
        # A disagreement about something not on the form can only be noted.
        editable = kind == "crosscheck" and any(f["input"].get("on_form", True) for f in facts)
        actions = ["acknowledge"] + (["set"] if editable else [])
    elif kind in ("unread", "overflow", "missing"):
        actions = ["set", "blank"]
    else:
        resolved = all(f.get("status") == "resolved" and f.get("value") not in (None, "") for f in facts)
        actions = (["confirm"] if resolved else []) + ["set", "blank"]
    if comparison:
        actions = ["set"] if comparison["kind"] == "disagreement" else ["acknowledge"]
        # Source context on a specificity card does not enable field correction or approval.
    docs = sorted({s["doc"] for f in facts for s in f.get("sources", []) if s["doc"] not in ("firm_profile.json", "paralegal_review", "office question")}
                  | {e["doc"] for e in evidence if e.get("doc")} | _alert_docs(flag, graph, kind))
    title = facts[0]["label"] if facts and kind not in ("unread",) else (evidence[0]["describe"] if evidence and evidence[0].get("describe") else flag.fact_key)
    if kind == "unread" and evidence and evidence[0].get("kind") == "choice":
        title = catalog.label(keys[0])
    if kind == "crosscheck" and port:
        title = "Last arrival in the U.S.: confirm the place the notice prints"
    elif kind == "crosscheck":
        title = ("Birthplace: city and state detail" if comparison and comparison["kind"] == "specificity" else
                 "Birthplace: choose the city for the current I-485" if comparison and comparison["kind"] == "disagreement" else
                 CROSSCHECK_TITLES.get(flag.fact_key) or f"{CONTEXT_LABELS.get(flag.fact_key, flag.fact_key)}: the client's answer differs from the document")
    elif kind == "missing" and facts and flag.fact_key.startswith("applicant.part9."):
        title = question_text(facts[0]["label"]) or "Part 9 follow-up question for the attorney"
    elif kind == "missing" and facts:
        short = facts[0]["short"]
        title = short + " (ask the client)"
    elif kind == "unsupported_language":
        title = "Questionnaire in a language without a question map"
    elif kind == "document_boundary":
        title = "Review document boundaries in Documents"
    elif kind == "document_subject":
        title = "Review whose facts these are in Documents"
    elif kind in ("alert", "blank_template"):
        # "ELIGIBILITY: folder shows ..." -> "ELIGIBILITY"; the full text is in messages
        title = flag.message.split(":", 1)[0] if ":" in flag.message[:40] else "Questionnaire missing"
        if re.match(r"Part 14 entry \d+ has no page, part or item", flag.message):  # batch.part14_spot_flags
            title = "Part 14 entry needs its page, part and item"
        title = {"CLIENT NOT SURE": "The client answered “I'm not sure”", "CLIENT TICKED": "The client ticked answers that may apply",
                 "ELIGIBILITY": "Eligibility: SIJS and marriage", "SERIOUS ANSWERS": "Removal-order answers to verify", "PRIOR FORMS": "Completed USCIS forms in the folder", "FILING BASIS": "Filing basis: more than SIJS in the folder", "EARLIER I-485": "An earlier I-485 was filed", "USCIS NOTICES TO ANSWER": "USCIS notices that need an answer", "CRIMINAL HISTORY": "Criminal history in the folder",
                 "REMOVAL PROCEEDINGS": "Removal proceedings (Notice to Appear)"}.get(title, title)
    if kind == "crosscheck" and flag.fact_key == arrival.CITY:  # the rule that picked, in its own words, with the attorney's approval
        info = rule_info(arrival.RULE)
        name = re.sub(r"\s*\(([^()]*)\)$", r": \1", str(info["name"]))  # a name that ends in its own parenthesis is not nested in another ("))")
        rule_note = f"The rule behind this ({name}): {info['plain_text']} {info['approval_text']}."
    else:
        rule_note = None
    return {
        "id": iid, "kind": kind, "level": flag.level, "title": title, "messages": ambiguity, "rule_note": rule_note,
        **({"comparison": comparison} if comparison else {}),
        "facts": facts, "evidence": evidence, "docs": docs, "actions": actions,
        "context": _context(flag, graph, catalog, kind), "qa": _client_answers(flag, graph, catalog, kind),
    }


def _name_item(iid: str, flag: Flag, graph: FactGraph, facts: list[dict[str, Any]], catalog: Catalog) -> dict[str, Any]:
    """The two name cards (src/name_events.py): the events side by side, each document open at its page, the product's pick and
    why, and one choice saved under the reviewer's name. "Which name is current?" chooses among the names the case carries; the
    attorney's "USCIS knows the client by another name" records the filing name the attorney accepts (an Undo reopens either)."""
    import name_events

    timeline = name_events.view(graph) or {"events": [], "names": [], "current": None, "uscis": []}
    fact = facts[0]
    question = timeline.get("question") or {}
    if flag.kind == "names" and (question.get("open") or timeline.get("stale")):
        # brief K6: the certificate prints no name after marriage (or the client says the name changed, or a later document came after a
        # person's choice). Nothing is picked for the reviewer; the name a person types goes in two EMPTY boxes (the spouse's surname is shown
        # beside them as information, never offered as a pick). A name a person typed before is typed again, never offered as a pick.
        wrote = timeline.get("client_wrote") or {}
        options = [n for n in timeline["names"] if n != timeline.get("typed_by_person")]
        labels = {n: (f"{n}: the client wrote this as the current legal name" if wrote.get("current") and name_events.same_name(n, wrote["current"])
                      else f"{n}: the name as it stands" if n == timeline["current"] else n) for n in options}
        fact["input"] = {"type": "choice", "options": options, "labels": labels, "on_form": True, "verbatim": True,
                         "feeds": "Part 1, Items 1 and 2 of every form"}
        fact["value"] = None
        spouse = (f"The spouse's family name on the marriage certificate: {question['spouse_family']}." if question.get("spouse_family")
                  else f"The spouse's name on the marriage certificate: {question['spouse_name']}." if question.get("spouse_name") else "")
        typed = []
        for key, help_text in ((name_events.CHOSEN_GIVEN, "Leave both boxes empty to use a name above. A name typed here wins over a choice above."),
                               (name_events.CHOSEN_FAMILY, (spouse + " Shown for information only: type it here only if a document or the client "
                                                                     "confirms it is part of the client's name now.").strip() if spouse
                                else "Type only what a document or the client confirms.")):
            box = _fact_view(graph, key, catalog) | {"value": None, "suggest": None}
            box["input"] = {"type": "text", "on_form": True, "feeds": "Part 1, Item 1 of every form"}
            box["help"] = {"text": help_text}
            typed.append(box)
        facts = facts + typed
        docs = [e["doc"] for e in timeline["events"] if e["from_document"]] + [question.get("doc")]
    elif flag.kind == "names":
        fact["input"] = {"type": "choice", "options": timeline["names"], "on_form": True, "verbatim": True, "feeds": "Part 1, Items 1 and 2 of every form"}
        fact["value"] = fact.get("value") or timeline["current"]
        docs = [e["doc"] for e in timeline["events"] if e["from_document"]]
    else:
        fact["input"] = {"type": "choice", "options": [timeline["current"]], "on_form": True, "verbatim": True, "feeds": "Part 1, Item 1 of every form"}
        fact["value"] = timeline["current"]
        docs = [e["doc"] for e in timeline["uscis"]] + [timeline["current_doc"]["doc"]]
        # a notice's spelling goes in Part 1, Item 2 only when the attorney lists it here (optional: left unticked, nothing is listed)
        also = _fact_view(graph, name_events.ALSO_KEY, catalog)
        also["input"] = {"type": "choice", "options": list(dict.fromkeys(e["name"] for e in timeline["uscis"])), "on_form": True,
                         "verbatim": True, "feeds": "Part 1, Item 2 (optional)"}
        facts = facts + [also]
    if flag.kind == "names":
        labels = dict(fact["input"].get("labels", {}))
        for event in timeline.get("events", []):
            if event.get("from_document") and (event.get("parent_key") or "").endswith(".name_after") and event.get("party") in {"party_a", "party_b"}:
                labels[event["name"]] = f"{event['name']}: printed Name after marriage, Party {event['party'][-1].upper()}"
        if labels:
            fact["input"]["labels"] = labels
    return {
        "id": iid, "kind": flag.kind, "level": flag.level, "title": NAME_CARDS[flag.kind], "messages": [], "facts": facts,
        "evidence": [], "docs": list(dict.fromkeys(d for d in docs if d)), "actions": ["set"], "context": [], "qa": [],
        "timeline": timeline,
    }


# A legal alert about documents in the folder: those documents, so the attorney can open them from the card.
ALERT_DOC_TYPES = {"CRIMINAL HISTORY": {"criminal_record"}, "REMOVAL PROCEEDINGS": {"notice_to_appear"},
                   "USCIS NOTICES TO ANSWER": {"uscis_notice"}, "EARLIER I-485": {"i485"}, "PRIOR FORMS": {"i485", "i765", "g28"}}


def _alert_docs(flag: Flag, graph: FactGraph, kind: str) -> set[str]:
    wanted = ALERT_DOC_TYPES.get(flag.message.split(":", 1)[0]) if kind == "alert" else None
    if not wanted:
        return set()
    return {s.doc_id for fact in graph.all_facts().values() for s in fact.sources if s.doc_type in wanted}


def question_text(label: str) -> str:
    """The form's own question, without its number or the instructions after it:
    '75. If you answered "Yes" to Item Number 74., was ... States? Select Yes. NOTE: ...' -> 'If you answered ... States?'"""
    text = re.sub(r"^\s*\d+\s*[A-Z]?\.\s*", "", label or "")
    return text[: text.index("?") + 1] if "?" in text else ""


def _context(flag: Flag, graph: FactGraph, catalog: Catalog, kind: str) -> list[dict[str, Any]]:
    """A Part 9 follow-up is asked because the main question is Yes: show that answer and where it came from."""
    from assemble import PART9_FOLLOWUPS

    if kind != "missing" or flag.fact_key not in PART9_FOLLOWUPS:
        return []
    base = _fact_view(graph, PART9_FOLLOWUPS[flag.fact_key][0], catalog)
    return [base | {"question": question_text(base["label"])}]


def _client_answers(flag: Flag, graph: FactGraph, catalog: Catalog, kind: str) -> list[dict[str, str]]:
    """The portal questions behind a "not sure" or "ticked" alert, each with the client's answer and the I-485 item it leaves blank."""
    if kind != "alert" or not flag.message.startswith(("CLIENT NOT SURE", "CLIENT TICKED")):
        return []
    unsure = flag.message.startswith("CLIENT NOT SURE")
    prefix = "questionnaire.unsure." if unsure else "questionnaire.yes."
    out: dict[str, dict[str, str]] = {}
    for key, fact in sorted(graph.all_facts().items()):
        if not key.startswith(prefix) or fact.value in (None, ""):
            continue
        question = re.sub(r"\s+[—–-]+\s+", ", ", str(fact.value)).strip()
        ref = catalog.ref(key.removeprefix(prefix)) or ""
        row = out.setdefault(question, {"question": question, "answer": "I'm not sure" if unsure else "Ticked: this applies to me",
                                        "from": "the client's portal questionnaire", "on_form": []})
        if ref and ref not in row["on_form"] and "Part " not in question:
            row["on_form"].append(ref)
    return [r | {"on_form": ", ".join(r["on_form"])} for r in out.values()]


def _form_keys(keys: list[str]) -> list[str]:
    """Questionnaire-only keys -> the I-485 boxes they feed (the mother's
    maiden-name line -> her given/family name at birth), so a reviewer's
    answer lands on the form and not in a background copy."""
    if keys and all(k.startswith("questionnaire.") and k in EVIDENCE_ALIASES for k in keys):
        return list(dict.fromkeys(a for k in keys for a in EVIDENCE_ALIASES[k]))
    return keys


def _foreign_fixes(by_key: dict[str, dict], graph: FactGraph) -> None:
    """On the "needs correcting" card the values are already saved, so the
    fix is offered as the other reading: the documented city, its state, the
    dates read the other way round."""
    import difflib

    from assemble import known_cities
    from extract.names import fold_name
    from extract.places import br_states_for_city

    city, province = by_key.get("applicant.last_foreign_city"), by_key.get("applicant.last_foreign_province")
    if city and city.get("value"):
        name = fold_name(str(city["value"]).split(",")[0])
        close = difflib.get_close_matches(name, known_cities(graph), n=1, cutoff=0.6)
        better = close[0] if close else name
        if better != city["value"]:
            city["alt"] = better
        country = (by_key.get("applicant.last_foreign_country") or {}).get("value") or ""
        from extract import geo

        if geo.code(country) not in (None, "BR"):  # Colombia, Haiti ...: that country's places (GeoNames)
            likely = geo.likely_region(country, better)
            if province and likely and geo.region(country, province.get("value") or "") != likely[0]:
                province["alt"] = likely[0]
        else:
            states = br_states_for_city(better)
            if province and len(states) == 1 and province.get("value") != states[0]:
                province["alt"] = states[0]
    for part in ("date_from", "date_to"):
        f = by_key.get(f"applicant.last_foreign_{part}")
        value = str((f or {}).get("value") or "")
        if f and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and int(value[8:]) <= 12 and value[5:7] != value[8:]:
            f["alt"] = f"{value[:4]}-{value[8:]}-{value[5:7]}"


def _foreign_help_elsewhere(country: str, region: dict | None, postal: dict | None, place: dict) -> None:
    """What the reviewer sees under a foreign address outside Brazil: where a
    region the client didn't write came from, a city that isn't a known place
    there, and how that country's postal codes work (extract/geo.py)."""
    from extract import geo

    code = (postal or {}).get("suggest") or (postal or {}).get("value") or ""
    city = place.get("suggest") or place.get("value") or ""
    word = geo.region_word(country)
    if region and not region.get("value") and region.get("suggest"):
        likely = geo.likely_region(country, city)
        if code and geo.region_from_postal(country, code) == region["suggest"]:
            why = f"the postal code {code} is in {region['suggest']}"
        elif likely and likely[0] == region["suggest"]:
            why = likely[1]
        else:
            why = f"{city} is in {region['suggest']}"
        region["help"] = {"text": f"Not written by the client: {why}. Check it."}
    elif region and not region.get("value") and not region.get("suggest") and city:
        options = geo.regions_for_place(country, city)
        if len(options) > 1:
            region["help"] = {"text": f"There are places called {city} in {len(options)} {word}s of {country.title()}: "
                                      f"{', '.join(options[:6])}{' ...' if len(options) > 6 else ''}. Ask the client which."}
    if city and not geo.regions_for_place(country, city):
        place["help"] = {"text": f"{city} isn't in GeoNames' list of places in {country.title()}: it may be a neighborhood or a "
                                 "misreading; enter the city or town."}
    if postal and not postal.get("value") and not postal.get("suggest"):
        info = geo.postal(country)
        if info is None:
            postal["help"] = {"text": f"{country.title()} doesn't use postal codes for most addresses: leave this blank unless "
                                      "the client has one."}
        else:
            text = f"The client didn't write a postal code ({info['label']}; {geo.postal_format(country)})."
            postal["help"] = ({"text": text + " Look it up from the street and city on the national post office's site:",
                               "url": info["lookup"], "link": f"{country.title()} postal code search"} if info.get("lookup")
                              else {"text": text + " Ask the client, or leave it blank."})


def _suggest_from_documents(facts: list[dict], graph: FactGraph) -> None:
    """Application helper with evidence-bound inputs."""
    import difflib

    from assemble import known_cities
    from extract.names import fold_name

    by_key = {f["key"]: f for f in facts}
    _foreign_fixes(by_key, graph)
    city = by_key.get("applicant.last_foreign_city")
    if city and city.get("suggest"):
        close = difflib.get_close_matches(fold_name(city["suggest"]), known_cities(graph), n=1, cutoff=0.6)
        if close and close[0] != fold_name(city["suggest"]):
            city["alt"], city["suggest"] = city["suggest"], close[0]
    # Province and postal code of a Brazilian foreign address: where they came from, or how to find them.
    for prefix in ("applicant.last_foreign_", "applicant.foreign_employer_"):
        country = (by_key.get(prefix + "country") or {}).get("suggest") or (by_key.get(prefix + "country") or {}).get("value")
        region, postal = by_key.get(prefix + "province"), by_key.get(prefix + "postal_code")
        place = by_key.get(prefix + "city") or {}
        from extract import geo

        if geo.code(country) not in (None, "BR"):
            _foreign_help_elsewhere(country, region, postal, place)
            continue
        if country != "BRAZIL":
            continue
        from extract.places import br_state_from_cep, br_states_for_city

        cep = (postal or {}).get("suggest") or (postal or {}).get("value") or ""
        city = place.get("suggest") or place.get("value") or ""
        if region and not region.get("value") and region.get("suggest"):
            if br_state_from_cep(cep) == region["suggest"]:
                why = f"the postal code {cep} is in {region['suggest']}"
            else:
                why = f"{city} is in {region['suggest']} (IBGE list of Brazilian municipalities)"
            region["help"] = {"text": f"Not written by the client: {why}. Check it."}
        if city and not br_states_for_city(city):
            # Fictional fixture or generic implementation note.
            state = (region or {}).get("suggest") or (region or {}).get("value")
            nearby = [c for c in known_cities(graph) + [fold_name(str(getattr(graph.get("applicant.last_foreign_city"), "value", "") or ""))]
                      if c and state in br_states_for_city(c)] if state else []
            place["help"] = {"text": f"{city} isn't a Brazilian municipality: it may be a neighborhood (BAIRRO/JARDIM); enter the city."
                                     + (f" Cities in {state} elsewhere in this file: {', '.join(dict.fromkeys(nearby))}." if nearby else "")}
        if postal and not postal.get("value") and not postal.get("suggest"):
            postal["help"] = {"text": "The client didn't write a postal code (CEP). Look it up from the street and city on the "
                                      "Brazilian post office's site:",
                              "url": "https://buscacepinter.correios.com.br/app/endereco/index.php", "link": "Correios CEP search"}
    left, since = by_key.get("applicant.last_foreign_date_to"), by_key.get("applicant.last_foreign_date_from")
    arrived = next((f.value for k in ("applicant.last_arrival_date", "applicant.i94_arrival_date", "applicant.last_arrival_date_self_reported")
                    if (f := graph.get(k)) is not None and f.status == "resolved"), None)
    if left and left.get("suggest") and arrived and re.fullmatch(r"\d{4}-\d{2}-\d{2}", left["suggest"]):
        y, m, d = left["suggest"].split("-")
        swapped = f"{y}-{d}-{m}"
        if int(d) <= 12 and swapped == arrived:
            left["suggest"] = swapped
            if since and since.get("suggest") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", since["suggest"]):
                sy, sm, sd = since["suggest"].split("-")
                if int(sd) <= 12:
                    since["suggest"] = f"{sy}-{sd}-{sm}"


def _suggest_names(facts: list[dict], evidence: list[dict]) -> None:
    """A name line the readers disagreed on: split each reading into the
    given/family boxes (firm convention). A name AT BIRTH whose first
    reading is empty is most likely unchanged -> NOT APPLICABLE, with the
    other reading one click away."""
    from extract.names import fold_name, looks_like_name, split_name

    ev = next((e for e in evidence if e.get("kind") == "text"), None)
    if ev is None:
        return
    source = next((k for k in (ev.get("facts") or {}).values() if k in EVIDENCE_ALIASES and "name" in k), None)
    if source is None:
        return
    reads = [str((r or {}).get("value") or "") for r in (ev.get("reads") or [])[:2]]
    splits = [split_name(r) if looks_like_name(fold_name(r)) else None for r in reads]
    at_birth = "birth_name" in source
    for f in facts:
        part = "given" if f["key"].endswith("given_name") else "family" if f["key"].endswith("family_name") else None
        if part is None:
            continue
        values = [getattr(sp, part) if sp else "" for sp in splits]
        first = values[0] if values else ""
        f["suggest"] = first or ("NOT APPLICABLE" if at_birth else "")
        other = next((v for v in values[1:] if v and v != f["suggest"]), None)
        if other:
            f["alt"] = other


def _answered_by_documents(flag: Flag, by_question: dict, graph: FactGraph) -> bool:
    """Application helper with evidence-bound inputs."""
    qid = flag.fact_key.split(".", 1)[1]
    _, ev = by_question.get(qid, (None, None))
    keys = list((ev or {}).get("facts", {}).values()) or ([ev["fact_key"]] if ev and ev.get("fact_key") else [])
    if not keys:
        return False
    if all(k.startswith("questionnaire.") for k in keys):
        targets = [t for k in keys for t in EVIDENCE_ALIASES.get(k, [])]
        return bool(targets) and all((f := graph.get(t)) is not None and f.status == "resolved" and f.value not in (None, "")
                                     for t in targets)
    # Fictional fixture or generic implementation note.
    # driver's license): the unreadable questionnaire line asks nothing.
    return all((f := graph.get(k)) is not None and f.status == "resolved" and f.value not in (None, "")
               and any(s.doc_type not in ("intake_questionnaire", "office_question") and s.doc_id != "paralegal_review" for s in f.sources)
               for k in keys)


_READER_WORDS = {"date_from": "the “from” date", "date_to": "the “to” date", "postal_code": "the postal code",
                 "province": "the state/province", "zip": "the ZIP code", "street": "the street", "city": "the city"}


# A source in words, never a file name: the same names the review screen uses (index.html SOURCE_NAMES).
SOURCE_NAMES = {"intake_questionnaire": "the client's questionnaire", "office_question": "the client's answer to the office's question", "portal": "the client's portal answers", "firm_profile": "the firm's details",
                "uscis_notice": "a USCIS notice", "birth_certificate": "the birth certificate", "passport": "the passport", "i94": "the I-94", "travel_history": "the travel history",
                "green_card": "the green card", "marriage_certificate": "the marriage certificate", "i360_approval": "the I-360 approval",
                "sij_order": "the court's SIJ order", "ssn_card": "the Social Security card", "drivers_license": "the driver's license",
                "work_permit": "the work permit (EAD)", "visa": "the visa", "notice_to_appear": "the Notice to Appear",
                "criminal_record": "the court or police record", "us_passport": "the U.S. passport", "citizenship_certificate": "the citizenship certificate",
                "us_birth_certificate": "the U.S. birth certificate", "tax_return": "the tax return", "w2": "the W-2", "pay_stub": "the pay stub",
                "bank_statement": "the bank statement", "lease": "the lease", "utility_bill": "the utility bill", "divorce_decree": "the divorce decree"}


def source_name(src: dict[str, Any]) -> str:
    return SOURCE_NAMES.get(src.get("type") or "") or ("the " + src["type"].replace("_", " ") if src.get("type") else re.sub(r"(?i)[.]pdf$", "", src["doc"]))


def _humanize(reason: str) -> str:
    reason = re.sub(r"^handwriting \w+: ", "", reason)
    for key, words in _READER_WORDS.items():
        reason = re.sub(rf"\b{key}\b", words, reason)
    return reason


RULE_NAMES = {
    "CRIM-01": "the criminal-record rule (a court record is in the folder)",
    "NTA-01": "the removal-proceedings rule (a Notice to Appear is in the folder)",
    "OVERSTAY-01": "the overstay rule (I-94 admit-until date vs. the I-360 date)",
    "NAME-01": "the married-name rule",
    "CITIZENSHIP-01": "the dual-citizenship rule",
    "ARRIVAL-01": "the last-arrival rule (a government paper over the client's answer)",
}


def rule_name(rule_id: str) -> str:
    return RULE_NAMES.get(rule_id) or rule_id.replace("POLICY:", "firm policy ")


def us_date(iso: str | None) -> str:
    """"2026-10-03T00:05:00+00:00" -> "10/02/2026": a stamp as the office's date (src/clock.py; 8:05 pm in Boston is already
    10/03 in UTC), a plain date as itself, in the screen's date form."""
    out = clock.us_date(iso)
    if out:
        return out
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)}" if m else ""


def source_text(source: str) -> str:
    """A rule's source as the screen says it, in a buyer's firm's terms: the rule or policy is practice built into the product from the
    first firm's filed forms (its recorded source is "firm practice, see decisions.md 2026-09-30"), not a decision of the firm reading
    the screen, and the date is the product's, not theirs. No file name, no "decision log"."""
    def built_in(m):
        return ("Built-in practice, taken from filed forms the product was built on (recorded " + us_date(m.group(1)) + "). "
                "It is not your firm's practice until an attorney approves it for every case")

    text = re.sub(r"^firm practice,? see decisions\.md (\d{4}-\d{2}-\d{2})$", built_in, (source or "").strip(), flags=re.I)
    text = re.sub(r"see decisions\.md (\d{4}-\d{2}-\d{2})", lambda m: "recorded " + us_date(m.group(1)), text)  # any other wording: no file name
    return text[:1].upper() + text[1:]


# Numbers that stay private in the review bundle and on any printed page: a Social Security number, an ITIN, a bank or card number.
# Matched on the fact key's last part ("applicant.ssn", "i751.spouse_ssn", "payment.routing_number"), never on a value's shape
# (an A-Number or a receipt number has the same digits and is not hidden).
_PRIVATE_KEY = re.compile(r"(?:^|_)(?:ssn|itin|tin|routing|iban|swift)(?:_|$)|(?:^|_)(?:bank|checking|savings)_?(?:account)?(?:_|$)|(?:^|_)(?:account|card|cc)_?(?:no|num|number)(?:_|$)", re.I)
_PRIVATE_LABEL = re.compile(r"social security n|\bSSN\b|individual taxpayer|\bITIN\b|routing number|account number|card number", re.I)


def is_private_number(key: str | None, label: str = "") -> bool:
    last = (key or "").rsplit(".", 1)[-1]
    if last.startswith(("uscis_online", "has_")):
        return False  # the USCIS online account number is printed on the G-28 and is not a financial or tax number; "has_ssn" is a Yes or No
    return bool(_PRIVATE_KEY.search(last)) or bool(label and "Online Account" not in label and _PRIVATE_LABEL.search(label))


def mask_number(value: Any) -> str:
    """"123-45-6789" or "123456789" -> "***-**-6789" (nine digits: a Social Security number's own grouping); any other length keeps
    the last four digits and hides the rest ("1234567890" -> "******7890"). Fewer than five digits are hidden whole."""
    text = str(value if value is not None else "").strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return text
    if len(digits) == 9:
        return "***-**-" + digits[-4:]
    return "*" * max(len(digits) - 4, 4) + (digits[-4:] if len(digits) > 4 else "")


def approval_text(approval: dict[str, Any], edit: dict[str, Any] | None = None) -> str:
    """The attorney's approval of a rule for every case, in words (src/rules/approval.py). edit: how the firm's version of a policy differs from
    the shipped one (rules.firm_policies.note): an approval that still holds is then an approval of the firm's own words, and says so."""
    if approval.get("state") == "approved" and edit and (edit.get("wording") or edit.get("answer")):
        return f"Approved as edited by the firm on {us_date(edit.get('edited_at') or edit.get('at'))} (approved by {approval['by']} on {us_date(approval['at'])})"
    if approval.get("state") == "approved":
        return f"Approved by {approval['by']} on {us_date(approval['at'])}"
    if approval.get("state") == "changed":
        return f"Changed since approval (approved by {approval['by']} on {us_date(approval['at'])}): not approved until an attorney approves it again"
    return "Not yet approved"


def rule_info(rule_id: str) -> dict[str, Any]:
    """A rule or firm policy as a person reads it: its name, plain text, source, and the attorney's approval for
    volume use -- the same words on the sign-off card, the source chips, Keeping current and the review bundle."""
    from rules import approval

    entry = next((r for r in approval.catalog() if r["id"] == rule_id), None)
    if entry is None:
        return {"id": rule_id, "code": rule_id.replace("POLICY:", ""), "kind": "policy" if rule_id.startswith("POLICY:") else "rule",
                "name": rule_name(rule_id), "plain_text": "", "source": "", "approval": {"state": "not_approved"}, "approval_text": "Not yet approved"}
    state = approval.status(rule_id, entry["hash"])
    # a policy's short name in plain words (schemas/law/policy_sijs.json "name"); the screen shows "name (code)"
    name = RULE_NAMES.get(rule_id) or entry.get("name") or ("Firm policy " + entry["code"] if entry["kind"] == "policy" else rule_id)
    edit = entry.get("edit")  # an attorney changed the firm's wording or answer in Settings (src/rules/firm_policies.py)
    return {"id": rule_id, "code": entry["code"], "kind": entry["kind"], "name": name, "plain_text": entry["plain_text"],
            "source": source_text(entry["source"]), "approval": state, "approval_text": approval_text(state, edit),
            "edited_text": edit["text"] if edit else None, "off": bool(edit and edit["off"])}


# validate.py's tier-3 note, in today's wording and in runs saved before it changed ("... -- needs sign-off.")
SIGN_OFF = re.compile(r"(?: --|:|\.) [Nn]eeds sign-off\.$")


def plain_message(flag: Flag, item: dict[str, Any]) -> str | None:
    """What the paralegal reads: plain English, no fact keys or reader
    jargon. The technical detail stays under "How this was read"."""
    m, kind = flag.message, flag.kind
    ev = item["evidence"][0] if item["evidence"] else {}
    if SIGN_OFF.search(m):
        return None  # tier 3: the card itself is the question
    rule = re.search(r"derived by rule (\S+)(?: --|:) needs a human sign-off", m)
    if rule:
        return f"Answered automatically by {rule_name(rule.group(1))}: needs a person's approval."
    if kind == "unread":
        inner = re.search(r"not read \((.*)\)(?: --|:) enter by hand", m)
        reason = inner.group(1) if inner else ""
        rule = next((f for f in item["facts"] if f.get("derived_by") and f.get("value") not in (None, "")), None)
        by_rule = (f" From the documents, {rule_name(rule['derived_by'])} filled in “{rule['value']}”"
                   + (f" ({rule['why']})" if rule.get("why") else "") + ": confirm or change it.") if rule else ""
        if ev.get("kind") == "choice":
            if "both readers see no mark" in reason:
                return "The client left this question unanswered on the questionnaire." + (by_rule or " Ask the client, or choose Leave blank.")
            if "disagree" in reason:
                return "The two checkbox readers disagree about which box is marked (" + re.sub(r"^the two readers disagree: ", "", reason) + "). Look at the scan and choose."
            if "not found" in reason:
                return "Some printed options couldn't be located on the scan: check the marks by eye and choose."
            return "The checkbox couldn't be read. Look at the scan and choose."
        status = ev.get("status")
        help_words = (" Portuguese words: RUA = street, AV/AVENIDA = avenue, EDIFÍCIO = building, BAIRRO/JARDIM = "
                      "neighborhood, APTO = apartment, SN (sem número) = no number, CEP = postal code, ESTUDANTE = student."
                      if ev.get("question") in ("last_foreign_address", "last_foreign_employer") else "")
        if status == "not_found":
            return ("The reader couldn't find this question on the questionnaire (the printed words are faint, or worded differently "
                    "on this copy). The page where it should be is shown: find the answer there and enter it.")
        if status == "disagree":
            return "The handwriting is hard to read: two readings didn't agree. The first reading is filled in; if the other one is right, click it. Check both against the scan." + help_words
        year_only = re.search(r"'(?:DE\s+)*(\d{4})'\s+is not a valid date", ev.get("reason") or reason)
        if status == "invalid" and year_only:
            return (f"The client wrote only the year ({year_only.group(1)}): no day or month. Ask the client for the full date, "
                    "or check the scan in case the rest is faint.")
        if status == "invalid":
            return f"The handwriting was read, but the result doesn't make sense ({_humanize(ev.get('reason') or reason)}). Check the scan and correct it." + help_words
        if status == "blank":
            return "The client left this blank."
        return "This answer couldn't be read. Check the scan and enter it."
    if "sources disagree" in m and item["facts"]:
        f = item["facts"][0]
        said = [f"{source_name(s)} says {s['value']}" for s in f.get("sources", []) if s["doc"] != "paralegal_review"]
        if f.get("derived_by") and f.get("value") is not None:
            said.append(f"{'the firm' + chr(39) + 's standard answer' if f['derived_by'].startswith('POLICY:') else 'a rule'} gives {f['value']}")
        said = "; ".join(said)
        return f"The answers don't agree: {said[:1].upper() + said[1:]}. Choose the right one; the I-485 box stays empty until you do."
    over = re.search(r"value (.*) is longer than the form allows \((\d+) characters", m)
    if over:
        return f"{over.group(1)} is too long for this box on the I-485 (at most {over.group(2)} characters). Enter a shorter version."
    follow = re.match(r"Part 9 (item [0-9.a-z]+) \(.*?\): the main question is answered Yes", m)
    if follow:
        return (f"The form asks {follow.group(1)} only when the main question above is Yes, and it is Yes. The attorney answers it "
                "(no rule fills it in).")
    if kind == "alert" and m.startswith("CLIENT NOT SURE"):
        return ("The I-485 boxes for these questions stay blank. Go over each one with the client (one short call), "
                "then Acknowledge with a note of what they said and what was decided.")
    if kind == "alert" and m.startswith("CLIENT TICKED"):
        return ("The client said these apply to them, so the matching Part 9 boxes stay blank. Read the client's explanation, "
                "decide each item, then Acknowledge with a note.")
    if kind == "alert":  # dates as the rest of the screen writes them: 07/13/2024
        return re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", r"\2/\3/\1", re.sub(r"^[A-Z0-9 -]+: ", "", m))
    return re.sub(r"^[a-z0-9_.]+: ", "", m)


def _apply_amendments(facts: list[dict], evidence: list[dict]) -> list[str]:
    """A word the writer crossed out and a word written above the line (questionnaire/handwriting.py second_look): the box carries the
    reading without the crossed-out word and with the other in its place, the reading the model first gave is the other reading a click
    away, and the card says what happened. -> the card's messages."""
    out = []
    for e in evidence:
        for sub, a in (e.get("amended") or {}).items():
            fact = next((f for f in facts if f["key"] == (e.get("facts") or {}).get(sub)), None)
            if fact is None:
                continue
            if fact.get("value") in (None, ""):
                fact["suggest"] = a["after"]  # not saved yet: the box holds the model's reading, for a person to check against the scan
            elif fact.get("value") != a["after"]:
                continue
            fact["alt"] = a["before"]
            struck, added = " and ".join(a["crossed_out"]), " and ".join(a["added"])
            out.append(f"{fact['short']}: the writer crossed out {struck}" + (f" and wrote {added} above the line, so the box has " if added else ", so the box leaves it out: ")
                       + f"{a['after']}. The reading the model first gave kept the crossed-out word: {a['before']}. "
                       "Check it against the scan; click the other reading to use that one.")
    return out


def _suggest_from_reads(facts: list[dict], evidence: list[dict]) -> None:
    """Pre-fill an unread handwriting item's inputs from the model's first
    read, so the reviewer corrects ("18/09/2005" -> 18/11/2005) instead of
    retyping the whole answer. Only a suggestion: nothing is recorded until
    the reviewer saves it."""
    from questionnaire.handwriting import parse_date, to_english

    choice = next((e for e in evidence if e.get("kind") == "choice" and e.get("suggest")), None)
    if choice is not None and len(facts) == 1 and len(choice["suggest"]) == 1:
        # the vision reader's answer where the two checkbox readers disagreed
        facts[0]["suggest"] = choice["suggest"][0]
        return
    ev = next((e for e in evidence if e.get("kind") == "text" and e.get("reads")), None)
    if ev is None:
        return
    def as_value(f, raw):
        if f["input"]["type"] == "date":
            return parse_date(raw) or ""
        if f["input"]["type"] == "choice":
            return raw if raw in f["input"]["options"] else ""
        return to_english(f["key"], raw.upper().strip())

    from questionnaire.handwriting import tidy_foreign_parts

    kind = ev.get("input_kind", "text")
    foreign = kind == "foreign_address" or any(k.startswith("applicant.foreign_employer") for k in (ev.get("facts") or {}).values())
    tidy = [tidy_foreign_parts(kind, {k: str(x).upper() for k, x in r.items() if x is not None}) if foreign else r
            for r in ev["reads"][:2]]
    from questionnaire.handwriting import KINDS, normalize

    def lenient(r):
        """The parts a reading WOULD give if accepted ("10 EXAMPLE STREET,
        APT 1, WORCESTER MA 01605" -> street / apt / city / state / ZIP),
        keeping the raw part where normalizing fails."""
        if kind not in KINDS:
            return r
        try:
            parts, _ = normalize(kind, {k: v for k, v in r.items() if v is not None and str(v).lower() != "null"},
                                 ev.get("date_order") or "dmy")
        except Exception:  # noqa: BLE001 -- a suggestion must never break the card
            return r
        return {**{k: v for k, v in r.items() if k not in parts}, **parts} if parts else r

    reads = [
        {fact_key: str(r[sub]) for sub, fact_key in (ev.get("facts") or {}).items() if r.get(sub) and str(r[sub]).lower() != "null"}
        for r in (lenient(r) for r in tidy)
    ]
    for f in facts:
        values = [as_value(f, r[f["key"]]) for r in reads if r.get(f["key"])]
        if values:
            f["suggest"] = values[0]
        # Fictional fixture or generic implementation note.
        # "... COSMO DA SILVA ..." vs "... ANTONIO DA SILVA ...").
        others = [v for v in values[1:] if v and v != values[0]]
        if others:
            f["alt"] = others[0]


def _group(item: dict[str, Any]) -> str:
    if item["level"] == "blocking":
        return "blocking"
    if item["kind"] == "names_uscis":
        return "attorney"  # the attorney decides (src/review/auth.py needs_attorney)
    if item["kind"] == "names":
        return "needs_input"
    if item["kind"] == "missing" and item["facts"] and item["facts"][0]["key"].startswith("applicant.part9."):
        return "attorney"
    if item["kind"] in ("unread", "overflow", "crosscheck", "missing") or any(f.get("status") == "conflict" for f in item["facts"]):
        return "needs_input"
    if item["kind"] == "alert":
        return "attorney"
    derived = {f.get("derived_by") for f in item["facts"] if f.get("derived_by")}
    if derived:
        return "rule:" + sorted(derived)[0]
    if any(s["type"] == "intake_questionnaire" for f in item["facts"] for s in f.get("sources", [])):
        return "questionnaire"
    return "other"
