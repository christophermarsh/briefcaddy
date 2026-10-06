"""The client has no such document: the office records it once, and the boxes, the gate and the checklist follow.

A filing asks about papers (a passport or travel document, a visa, the I-94, a birth certificate, a court order). When the folder lacks
one and the client has none, a paralegal records that on the Documents tab, with a reason from a short list, and the product does four
things from that one mark:

  - the boxes that ask about the paper read NOT APPLICABLE, never blank: marked_value decides, from the mark's marker fact in the graph
    (case.absent.<paper>), and the fill step (src/fill/field_map.py map_facts_to_fields, used by every form: the I-485's item 10 and item 12,
    the work permit's, the I-360's, the I-589's ...) writes the word on the form. The case's facts stay EMPTY, so no rule or filing reads a
    placeholder as an answer. If the case holds a value for any box of the paper, none of its boxes read as absent and a finding is raised;
  - the packet's gate (src/packet.py plan): a required paper passes only where the filing's schema says it may be absent, with the
    instruction line it rests on (schemas/packets/*.json "may_be_absent", the lines in schemas/registers/absence_papers.json); every other
    required paper stays blocking with the sentence NEEDED, and only the attorney's mailing override (src/prefile.py) goes past it;
  - the checklist says "not available: <reason>" for the paper, and the plan lists every blank box that has neither a value nor a mark;
  - the client's request for the paper, if the portal asked for it, is closed ("The office has what it needs").

The mark is a review decision (decisions.json, item "absent:<paper>"): who and when, the ledger row, the review bundle, Undo. The
client's own answer ("Do you have an I-94? No") is shown beside the mark as the client's word and is never the mark. A paper that
arrives and is classified lifts the mark by itself (lift_arrived, called whenever the documents record is written): a ledger row, the
boxes fill from the paper, and the Documents tab says so.

"The office will get it later" is a reminder, not an absence: it never writes NOT APPLICABLE and never passes a gate.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import clock
import schema_path

REPO = Path(__file__).resolve().parents[1]
SCHEMA = schema_path.path("register", "absence_papers")
PREFIX = "absent:"  # the decision's item id: absent:<paper>
MARKER = "case.absent."  # the marker fact in the graph: case.absent.<paper> = the reason's id
SYSTEM = "The document reader"  # who lifts a mark when the paper arrives
NEEDED = "This paper cannot be marked absent: the filing needs it."
LATER_TEXT = "The paper is marked as coming later: it has to be in the folder before the packet is final."
ANOTHER_PERSONS = ("marriage_certificate", "divorce_decree")  # papers that are about two people: whoever they are set to, they are in the folder


# -- the vocabulary -------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def vocab(path: Path = SCHEMA) -> dict[str, Any]:
    return _load(str(path), path.stat().st_mtime)


def papers() -> dict[str, dict[str, Any]]:
    return vocab()["papers"]


def reasons() -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in vocab()["reasons"]}


def name(paper: str) -> str:
    return papers()[paper]["name"]


def plain(paper: str) -> str:
    """The paper in the middle of a sentence: "passport or travel document", "U.S. visa", "Form I-94"."""
    return papers()[paper].get("plain") or papers()[paper]["name"].lower()


def title(item_id: str) -> str:
    """"The client has no passport or travel document", for the Decision log and the Done list."""
    paper = item_id[len(PREFIX):]
    return f"The client has no {plain(paper)}" if paper in papers() else "The client has no such document"


def line_text(line_id: str) -> str:
    """An instruction line as one sentence for the screen and for the attorney: which form's instructions, the edition, the page, the words."""
    line = vocab()["lines"][line_id]
    page = f", page {line['page']}" if line.get("page") else ""
    if not line.get("edition"):  # a line from the form's own box text, not its instructions
        return f"Form {line['form']}: {line['says']}"
    return f"Form {line['form']} Instructions, edition {line['edition']}{page}: {line['says']}"


def reason_words(reason: str) -> str:
    """"lost or destroyed": the reason in the middle of a sentence."""
    label = reasons().get(reason, {}).get("label", reason)
    return label[:1].lower() + label[1:]


# -- what is in the folder ------------------------------------------------------------------------------------------------


def _counts(record: dict[str, Any], paper: str) -> bool:
    """Whether a document record is this paper, as the client's own: a document a person set to someone else (the spouse's passport) is not."""
    if record.get("type") not in papers()[paper]["doc_types"]:
        return False
    if paper in ANOTHER_PERSONS:
        return True
    return not (record.get("person_set_by") and record.get("person") not in ("applicant", "unknown", None, ""))


def in_folder(client_dir: str | Path, records: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """{paper: the document records that are it} for each paper the folder holds, from the document record (documents.json): the
    classified documents of the case; a case with no record yet reads meta.json's classifications."""
    client_dir = Path(client_dir)
    if records is None:
        import documents

        records = (documents.read(client_dir) or {}).get("documents")
    if records is None:
        meta = client_dir / "meta.json"
        classes = (json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}).get("classifications") or {}
        records = [{"id": doc, "type": kind, "files": [doc]} for doc, kind in classes.items()]
    return {paper: found for paper in papers() if (found := [r for r in records if _counts(r, paper)])}


# -- the marks ------------------------------------------------------------------------------------------------------------


def _view(decision: dict[str, Any]) -> dict[str, Any]:
    said = decision.get("absence") or {}
    return {"paper": said.get("paper"), "reason": said.get("reason"), "reason_text": reason_words(said.get("reason") or ""), "line": said.get("line") or "",
            "by": decision.get("reviewer"), "role": decision.get("role"), "at": decision.get("at"), "on": clock.us_date(decision.get("at"))}


def marks(client_dir: str | Path, decisions: dict[str, dict[str, Any]] | None = None, records: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """{paper: the mark} for every mark in force: recorded, not undone, and the paper still not in the folder (one that arrived, whether or not the
    lift has run, is not an absence any more)."""
    from review.state import load_decisions

    client_dir = Path(client_dir)
    decisions = load_decisions(client_dir) if decisions is None else decisions
    found = {iid[len(PREFIX):]: d for iid, d in decisions.items() if iid.startswith(PREFIX) and d.get("action") == "absent" and iid[len(PREFIX):] in papers()}
    if not found:
        return {}
    here = in_folder(client_dir, records)
    return {paper: _view(d) | {"paper": paper} for paper, d in found.items() if paper not in here}


def without_arrived(client_dir: str | Path, decisions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The decisions in force with the absence marks of papers that have arrived left out: what the case's graph is built from."""
    if not any(iid.startswith(PREFIX) for iid in decisions):
        return decisions
    kept = marks(client_dir, decisions)
    return {iid: d for iid, d in decisions.items() if not iid.startswith(PREFIX) or iid[len(PREFIX):] in kept}


def absent_types(client_dir: str | Path) -> set[str]:
    """The document types the office has marked absent, for the portal: the client is not asked for them. A reason that says the paper exists ("the office
    will get it later": boxes false) is not an absence: the client's request stays open."""
    return {t for paper, m in marks(client_dir).items() if reasons().get(m["reason"], {}).get("boxes", True) for t in papers()[paper]["doc_types"]}


def boxes(paper: str) -> list[str]:
    return [b["key"] for b in papers()[paper]["boxes"]]


@lru_cache(maxsize=2)
def _box_index(mtime: float) -> dict[str, tuple[str, dict[str, Any]]]:
    return {b["key"]: (paper, b) for paper, spec in vocab()["papers"].items() for b in spec["boxes"]}


def box_keys() -> list[str]:
    """The fact keys of every box any paper's mark answers."""
    return list(_box_index(SCHEMA.stat().st_mtime))


def box_value(box: dict[str, Any], form_id: str | None = None) -> str:
    """What the box reads when the paper is marked absent: the firm's NOT APPLICABLE, N/A in a box too short for it, or the word one form's own
    instructions give (the I-589's I-94 box: None)."""
    return (box.get("values") or {}).get(form_id or "") or box["value"]


def holds_value(graph, paper: str) -> bool:
    """Whether the case holds a value for any box of the paper (a number from an I-94, what the client typed)."""
    return any((f := graph.get(b["key"])) is not None and f.status == "resolved" and f.value not in (None, "") for b in papers()[paper]["boxes"])


def marked_value(graph, key: str, form_id: str | None = None) -> str | None:
    """What a box of a paper marked absent reads when the form is filled, else None. This is the only place the placeholder comes from, and it is used
    where a form is filled (src/fill/field_map.py): the case's facts stay empty, so no rule or filing reads NOT APPLICABLE as an answer. Nothing is written
    when the reason says the paper exists, when the case holds a value for ANY box of the paper (then none of its boxes read as absent: a half-true block
    is worse than a finding, src/assemble.py consistency_findings), or when the box was settled in review (a reviewer's blank)."""
    found = _box_index(SCHEMA.stat().st_mtime).get(key)
    if found is None:
        return None
    paper, box = found
    marker = graph.get(MARKER + paper)
    if marker is None or marker.status != "resolved" or not marker.value or reasons().get(str(marker.value), {}).get("boxes") is False:
        return None
    if holds_value(graph, paper):
        return None
    fact = graph.get(key)
    if fact is not None and fact.status != "missing":
        return None
    return box_value(box, form_id)


def mark(client_dir: str | Path, paper: str, reason: str, line: str, who: str, role: str | None = None) -> dict[str, Any]:
    """Records that the client has no such paper: a decision with who, when and why (the Decision log, the ledger, the review bundle, Undo)."""
    from review.state import record_decision

    who = (who or "").strip()
    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    if paper not in papers():
        raise ValueError("Choose which paper the client has none of.")
    if reason not in reasons():
        raise ValueError("Choose why the client has no such document.")
    if paper in in_folder(client_dir):
        raise ValueError(f"The {plain(paper)} is in the folder: there is nothing to record.")
    line = " ".join(str(line or "").split())[:200]
    label = reasons()[reason]["label"]
    item = {"id": PREFIX + paper, "kind": "absence", "level": "informational", "group": "documents", "actions": ["absent"], "title": name(paper),
            "facts": [{"key": key} for key in boxes(paper)]}
    record_decision(Path(client_dir), item, {"action": "absent", "reviewer": who, "role": role, "note": label + (f": {line}" if line else ""),
                                              "absence": {"paper": paper, "reason": reason, "line": line}})
    return marks(client_dir)[paper]


def undo(client_dir: str | Path, paper: str, who: str, role: str | None = None) -> None:
    """Takes the mark back: the boxes go back to what the case holds, and the paper is asked of the client again."""
    from review.state import undo_decision

    if not (who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    if paper not in papers():
        raise ValueError("Choose which paper to take the mark off.")
    undo_decision(Path(client_dir), PREFIX + paper, who, role)


def lift_arrived(client_dir: str | Path, records: list[dict[str, Any]] | None = None) -> list[str]:
    """A paper that arrived and was classified ends the office's mark that the client had none: the mark is undone by the document reader, with a ledger
    row, so the boxes fill from the paper at the next filling and the Documents tab says so. Called whenever the documents record is written.
    Returns the papers lifted."""
    from review.state import load_decisions, undo_decision

    client_dir = Path(client_dir)
    decisions = load_decisions(client_dir)
    found = {iid[len(PREFIX):] for iid, d in decisions.items() if iid.startswith(PREFIX) and d.get("action") == "absent"}
    if not found:
        return []
    here = in_folder(client_dir, records)
    lifted = sorted(p for p in found if p in here and p in papers())
    for paper in lifted:
        undo_decision(client_dir, PREFIX + paper, SYSTEM, "system", lifted=f"The {plain(paper)} arrived and was read: the mark that the client has none is lifted")
    return lifted


def lifted_marks(client_dir: str | Path) -> dict[str, dict[str, Any]]:
    """{paper: who lifted it and when} for a mark the arrival of the paper ended, while the paper is still in the folder."""
    from review.state import load_decision_log

    client_dir = Path(client_dir)
    out = {}
    for iid, d in load_decision_log(client_dir).items():
        undone = d.get("undone")
        if iid.startswith(PREFIX) and undone and undone.get("by") == SYSTEM and iid[len(PREFIX):] in papers():
            out[iid[len(PREFIX):]] = {"on": clock.us_date(undone.get("at")), "marked_by": d.get("reviewer"), "marked_on": clock.us_date(d.get("at"))}
    return out


# -- the filing: which papers it asks about, and whether it may do without one --------------------------------------------


@lru_cache(maxsize=2)
def _maps(mtime: float) -> dict[str, set[str]]:

    forms = json.loads((schema_path.path("packet", "companion_forms")).read_text(encoding="utf-8"))["forms"]
    out = {fid: set(form.get("map", {})) for fid, form in forms.items()}
    for fid, form in forms.items():
        if form.get("map_from") in out:
            out[fid] |= out[form["map_from"]]
    out["i485"] = set(json.loads((schema_path.path("field_map", "i485")).read_text(encoding="utf-8")).get("fact_to_acroform", {}))
    return out


def form_keys(schema: dict[str, Any]) -> set[str]:
    """The fact keys the filing's forms are filled from (the I-485's map, and each companion form's)."""
    import re


    maps = _maps((schema_path.path("packet", "companion_forms")).stat().st_mtime)
    keys: set[str] = set()
    for fid in schema.get("forms", ["i485"]):
        keys |= maps.get(fid) or maps.get(re.sub(r"_\d+$", "", fid)) or set()
    return keys


def asked(schema: dict[str, Any]) -> list[str]:
    """The papers the filing asks about, in the vocabulary's order: one an exhibit takes, or one a form's box asks about."""
    types = {t for ex in schema.get("exhibits", []) for t in ex.get("types", [])}
    keys = form_keys(schema)
    return [p for p, spec in papers().items() if types & set(spec["doc_types"]) or keys & {b["key"] for b in spec["boxes"]}]


def exhibits_of(schema: dict[str, Any], paper: str) -> list[dict[str, Any]]:
    return [ex for ex in schema.get("exhibits", []) if set(ex.get("types", [])) & set(papers()[paper]["doc_types"])]


def need(schema: dict[str, Any], paper: str) -> dict[str, Any]:
    """What the filing needs of the paper: "needed" (a required exhibit takes it and no instruction line says it may be absent), "may_be_absent" (with the
    line), "optional" (an exhibit takes it, not required) or "boxes" (only a form's boxes ask about it)."""
    found = exhibits_of(schema, paper)
    if not found:
        return {"need": "boxes", "line": None}
    for ex in found:
        line_id = (ex.get("may_be_absent") or {}).get(paper)
        if line_id:
            return {"need": "may_be_absent", "line": line_text(line_id), "instead": vocab()["lines"][line_id].get("instead"), "exhibit": ex["id"]}
    if any(ex.get("required") for ex in found):
        return {"need": "needed", "line": None, "exhibit": next(ex["id"] for ex in found if ex.get("required"))}
    return {"need": "optional", "line": None, "exhibit": found[0]["id"]}


def judge(exhibit: dict[str, Any], marked: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The gate's verdict on one required exhibit the folder has nothing for: {"state": "open"} (no paper of it is marked: it is missing, as before),
    {"state": "pass", "papers": [...]} (a paper the filing's schema says may be absent is marked absent with a reason) or {"state": "blocked", "sentence"}
    (marked, but the filing cannot do without it, or only marked as coming later)."""
    ours = [p for p in papers() if set(exhibit.get("types", [])) & set(papers()[p]["doc_types"]) and p in marked]
    if not ours:
        return {"state": "open"}
    allowed = exhibit.get("may_be_absent") or {}
    passing = [p for p in ours if p in allowed and reasons().get(marked[p]["reason"], {}).get("boxes", True)]
    if passing:
        return {"state": "pass", "papers": passing}
    if any(marked[p]["reason"] == "office_later" for p in ours):
        return {"state": "blocked", "sentence": LATER_TEXT}
    return {"state": "blocked", "sentence": NEEDED}


def checklist(schema: dict[str, Any], marked: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """The packet's checklist lines for every paper the filing asks about that is marked: "not available: <reason>" (what the instructions say to
    add instead, when they say), or the reminder that the office will get it later."""
    out = []
    for paper in asked(schema):
        m = marked.get(paper)
        if not m:
            continue
        who = f"Marked by {m['by']}" + (f" on {m['on']}" if m.get("on") else "")
        said = f" ({m['line']})" if m.get("line") else ""
        if m["reason"] == "office_later":
            out.append({"kind": "missing", "text": f"{name(paper)}: the office will get it later{said} ({who}). Add it to the folder before the packet is final: the mark lifts itself."
                                                   })
            continue
        n = need(schema, paper)
        spec = papers()[paper]
        boxes_said = (" The boxes that ask about it read NOT APPLICABLE" + (" (N/A where a box is too short for that)" if any(b["value"] == "N/A" for b in spec["boxes"]) else "") + ".") \
            if spec["boxes"] else ""
        out.append({"kind": "check", "text": f"{name(paper)}: not available: {m['reason_text']}{said}. {who}.{boxes_said}"
                                             + (f" {n['instead']}" if n.get("instead") else "")})
    return out


# -- the Documents tab ------------------------------------------------------------------------------------------------------


def _client_words(graph, paper: str) -> list[dict[str, str]]:
    """The client's own answers about the paper, as the client's word: the questionnaire's answer, and a box the client typed. Shown beside the row,
    never the mark."""
    out = []
    for said in papers()[paper]["client_said"]:
        fact = graph.get(said["key"]) if graph is not None else None
        if fact is not None and fact.status == "resolved" and fact.value not in (None, ""):
            out.append({"label": said["label"], "value": str(fact.value)})
    for box in papers()[paper]["boxes"]:
        fact = graph.get(box["key"]) if graph is not None else None
        if fact is None or fact.status != "resolved" or fact.value in (None, ""):
            continue
        typed = [s for s in fact.sources if s.doc_type in ("intake_questionnaire", "office_question", "portal") or s.doc_id in ("portal questionnaire", "portal", "office question")]
        if typed and str(fact.value) not in ("NOT APPLICABLE", "N/A"):
            out.append({"label": f"The client wrote the {box['label']}", "value": str(typed[-1].normalized_value)})
    return out


FORM_WORDS = {"i589": "I-589"}


def _reads(box: dict[str, Any]) -> str:
    """A box in words for the Documents tab: "I-94 number (reads N/A; the I-589's box reads None)"; a box that reads NOT APPLICABLE needs no note."""
    notes = ([f"reads {box['value']}"] if box["value"] != "NOT APPLICABLE" else []) + [f"the {FORM_WORDS.get(f, f)} box reads {v}" for f, v in (box.get("values") or {}).items()]
    return box["label"] + (f" ({'; '.join(notes)})" if notes else "")


def listing(client_dir: str | Path, filing: str | None, graph=None) -> dict[str, Any]:
    """"Papers this filing asks about": each with whether it is in the folder, marked absent (who, when, why), or neither; what the filing needs of it;
    and the client's own word beside it."""
    import documents
    import packet

    client_dir = Path(client_dir)
    schema = packet.for_case(packet.load_filing(filing), client_dir)
    marked, held, lifted = marks(client_dir), in_folder(client_dir), lifted_marks(client_dir)
    rows = []
    for paper in asked(schema):
        n = need(schema, paper)
        mark_ = marked.get(paper)
        state = "in_folder" if paper in held else "marked" if mark_ else "missing"
        row = {"paper": paper, "name": name(paper), "plain": plain(paper), "state": state, "need": n["need"], "line": n.get("line"), "instead": n.get("instead"),
               "documents": [{"id": r.get("id"), "name": documents.name(r.get("type") or "unclassified"), "doc": (r.get("doc_ids") or r.get("files") or [""])[0]}
                             for r in held.get(paper, [])],
               "mark": mark_, "client_said": _client_words(graph, paper),
               "lifted": lifted.get(paper) if paper in held else None, "boxes": [_reads(b) for b in papers()[paper]["boxes"]],
               "box_lines": [line_text(x) for x in papers()[paper].get("box_lines", [])]}
        row["blocking"] = NEEDED if state == "marked" and n["need"] == "needed" else ""
        rows.append(row)
    return {"filing": filing or "i485", "title": packet.filing_title(filing), "papers": rows,
            "reasons": [{"id": r["id"], "label": r["label"]} for r in vocab()["reasons"]]}


# -- the blank boxes ------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _i485_catalog():
    from fill import load_field_map
    from review.state import Catalog

    return Catalog(load_field_map(schema_path.path("field_map", "i485")), schema_path.path("template", "i485"))


@lru_cache(maxsize=8)
def _tooltips(template: str) -> dict[str, str]:
    from fill import template_cache

    return {name_: str(f.get("/TU") or "") for name_, f in template_cache.fields(template).items()}


_VERB = r"(?:Enter|Select|Provide|Type|Check|Choose|If)\b"
_ITEM_BOX = re.compile(r"^Part\s+(\d+)\..*?(?<![\w.])(\d{1,2})\.\s?(?:([A-Za-z])\.?\s)?" + _VERB)
_LAST_ITEM = re.compile(r"(?<![\w.])\d{1,2}\.\s?(?:[A-Za-z]\.?\s)?" + _VERB)


def _tooltip_place(tip: str) -> str:
    """"Part 2, Item 18" or "Part 2, Item 5.b" from a box's own text ("Part 2. ... 5. B. Enter Street Number and Name."), only when the text says the item
    number right before what to enter; any other shape gives nothing: a place left off is better than a wrong one."""
    m = _ITEM_BOX.match(tip)
    return f"Part {m.group(1)}, Item {m.group(2)}" + (f".{m.group(3).lower()}" if m.group(3) else "") if m else ""


def _tooltip_label(tip: str) -> str:
    """"Passport Number" from "... 18. Enter Passport Number."; nothing when the text does not end in that shape."""
    found = list(_LAST_ITEM.finditer(tip))
    if not found or found[-1].group(0).endswith("If"):
        return ""
    words = tip[found[-1].end():].strip(" .")
    return words[:1].upper() + words[1:]


@lru_cache(maxsize=64)
def _companion_boxes(form_id: str) -> dict[str, tuple[str, str]]:
    """{fact key: (the Part and Item of its first box, its words)} for a companion form, from the form's own box text, worked out once per form (the
    template is opened once). Either may be empty: the box text does not say."""
    from fill.companion import field_map_for, load_profile
    from review.state import tidy_tooltip

    form = load_profile()["forms"][form_id]
    tips = _tooltips(str(schema_path.named(form["template"])))
    out = {}
    for key, spec in field_map_for(form).items():
        if spec.get("fields"):
            tip = tidy_tooltip(tips.get(spec["fields"][0], ""))
            out[key] = (_tooltip_place(tip), _tooltip_label(tip))
    return out


def _place(form_id: str, key: str) -> str:
    """"Part 1, Item 10": where the box a fact fills sits on the form, from the form's own box text; nothing when it cannot be told."""
    for spec in papers().values():  # a box of a paper: the item the form's instructions give it (the I-485's item 10 box text carries no item number)
        for b in spec["boxes"]:
            if b["key"] == key and (b.get("where") or {}).get(form_id):
                return b["where"][form_id]
    try:
        return _i485_catalog().ref(key) if form_id == "i485" else _companion_boxes(form_id).get(key, ("", ""))[0]
    except Exception:  # noqa: BLE001 -- a place that cannot be worked out is left off the line, never a failed packet screen
        return ""


def box_label(key: str) -> str | None:
    """The words for a box a paper's mark answers ("the passport or travel document number"), else nothing."""
    for spec in papers().values():
        for b in spec["boxes"]:
            if b["key"] == key:
                return b["label"]
    return None


def paper_of(key: str) -> str | None:
    return next((p for p, spec in papers().items() if any(b["key"] == key for b in spec["boxes"])), None)


def blank_boxes(client_dir: str | Path, schema: dict[str, Any], companions: dict[str, Any], graph=None) -> list[dict[str, str]]:
    """Every box of the filing's filled forms the product knows is needed that has neither a value nor a mark, by form and item:
    [{"form", "form_id", "where", "what", "fix"}]. The I-485's are the paper boxes the filing asks about and the boxes the completeness check lists
    (src/assemble.py); a companion form's are what it left blank when it was filled (companions.json)."""
    import assemble

    out: list[dict[str, str]] = []
    forms = {fid: form for fid, form in _forms(schema)}

    def put(fid: str, key: str, what: str, fix: str | None = None) -> None:
        paper = paper_of(key)
        fix = fix or (f"Add the {plain(paper)} to the folder, or record on the Documents tab that the client has none." if paper else
                      "Settle it in review, or fill it in by hand.")
        out.append({"form": forms[fid], "form_id": fid, "where": _place(fid, key), "what": what, "fix": fix, "key": key})

    if "i485" in forms and graph is not None:
        def has(key: str) -> bool:
            fact = graph.get(key)
            return (fact is not None and fact.status == "resolved" and fact.value not in (None, "")) or marked_value(graph, key, "i485") is not None

        wanted = {key: box_label(key) or re.sub(r"\.? ?[Aa]sk the client.*$", "", ask).strip(" .:") for key, ask in assemble.completeness_findings(graph)}
        for paper in asked(schema):
            for box in papers()[paper]["boxes"]:
                if box["key"] in form_keys({"forms": ["i485"]}) and not has(box["key"]):
                    wanted.setdefault(box["key"], box["label"])
        for key, what in wanted.items():
            if not has(key):
                put("i485", key, what[:1].upper() + what[1:])
    for fid, result in companions.items():
        if fid not in forms or fid == "i485":
            continue
        boxes_of = _companion_boxes_safe(fid)
        for key in (result or {}).get("left_blank", []):
            put(fid, key, _words_for(fid, key, boxes_of))
        for key in (result or {}).get("no_option", []):  # a list on the form that has no such choice: the box is empty and nobody has been told
            put(fid, key, _words_for(fid, key, boxes_of), fix="The form's list has no choice for what the case says, so the box is empty: choose from the list by hand, "
                                                              "or leave it empty if the client has none.")
    return out


def _companion_boxes_safe(form_id: str) -> dict[str, tuple[str, str]]:
    try:
        return _companion_boxes(form_id)
    except Exception:  # noqa: BLE001 -- words that cannot be read from the form are made from the key: never a failed packet screen
        return {}


def _words_for(form_id: str, key: str, boxes_of: dict[str, tuple[str, str]]) -> str:
    """What the box is called on a list: a paper's box by its own words, else the form's own box text, else the last part of the key."""
    import packet

    tail = key.split(".", 1)[-1]
    said = box_label(key) or boxes_of.get(key, ("", ""))[1] or re.sub(r"^the ", "", packet._FACT_NAMES.get(tail, "")) or tail.replace("_", " ")
    return said[:1].upper() + said[1:]


def _forms(schema: dict[str, Any]) -> list[tuple[str, str]]:
    """[(form id, the form's short name)] of the filing: "I-485", "I-765"."""
    import packet

    return [(fid, form["short"]) for fid, form in packet.forms_in(schema) if fid != "g28" and not fid.startswith("g28_")]
