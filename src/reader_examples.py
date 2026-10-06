"""The firm's labelled examples of what the readers read (brief M1): the reader measured per field, on the firm's own documents.

An example is a by-product of work the office already does, never a click of its own. Each time a person confirms a value a reader read
from a document on a review card, or types a correction over it, or the firm's hand-filled form (the monthly audit of what the office
changes, src/audit_fill.py) differs from a box a reader filled, one small file is written: which reader, which field (the fact key), which
document and page, what the reader read (as printed and as compared), the value the office kept, where on the page the read sat when the
OCR word boxes say so (else a sentence that no box is known), who and when, and whether the read value was kept or changed.

WHERE. data/reader_examples/<case id>/<example id>.json (I485_READER_EXAMPLES points elsewhere), one plain file per example, the folders
owner-only (0700) and every file 0600, beside the case folders like the firm's other files. Each example sits in its case's own folder and
carries the case id: it holds a client's values, so it is case data under the same gate as the case.

THE GATE. Nothing reads an example without saying whose eyes it is for: every(clients_root, may_open) takes the reader's own check
(src/restricted.py visible_to, or unprotected() for a page every member of staff reads), and a case the reader may not open is in no
example, count or line. An example whose case folder is gone is read by nothing.

WHAT IS NOT AN EXAMPLE. A value the client typed (the portal's answers, the answers to the office's questions), a value a person typed
where no reader read anything, the firm's own details, a rule's output, an acknowledgement, a "leave blank", an absence mark: no reader
read it, so it says nothing about a reader.

THE END OF A CASE. The client's file handed over (src/engagement.py export_file, through tools/export_firm.py gather) carries the case's
examples; an attorney's record that the case's folder was removed after its keeping date (src/engagement.py mark_destroyed) removes them
with it; a decision taken back marks its examples undone (counted nowhere, kept on file like the decision itself).

NOTHING LEARNS. No model is trained, called or changed here. The examples exist so a later release can measure a reader against them
before any change to it, and so the accuracy page (docs/accuracy.md) and the morning report can say which fields the office changes most.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import clock

ENV = "I485_READER_EXAMPLES"
FOLDER = "reader_examples"
VERSION = 1
KEPT, CHANGED = "kept", "changed"
HOW = {"confirm": "Confirmed on a review card", "correction": "Corrected on a review card", "hand_filled_form": "The firm's hand-filled form differs"}
NO_BOX = ("No word box is known for this read: this reader reads the document's text, not word boxes on the page image, so no crop is "
          "given rather than a guessed one.")
BOX_FROM = "The OCR word boxes of the scanned page (src/classify/ocr.py), in the page image's pixels: left, top, right, bottom."
FORM_WHO = "The firm's hand-filled form"  # the audit's examples: the office's own filed form is the final value
MORNING_MIN = 20  # a field needs this many examples before the morning report names it
MORNING_RATE = 0.10  # and more than this share of them changed
MORNING_WORST = 3
WORST = 10
PROTECTED_NOTE = "Protected cases are not counted here: their examples are read only by the people who may open them."


# -- where -----------------------------------------------------------------------------------------------------------------------------------


def root(clients_root: str | Path) -> Path:
    """I485_READER_EXAMPLES, else data/reader_examples beside the case folders."""
    env = os.environ.get(ENV)
    return Path(env) if env else Path(clients_root).resolve().parent / FOLDER


def case_folder(client_dir: str | Path) -> Path:
    """The case's own examples folder: data/reader_examples/<case id>/."""
    client_dir = Path(client_dir)
    return root(client_dir.resolve().parent) / client_dir.name


def _private_dir(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for f in (folder.parent, folder):
        try:
            os.chmod(f, 0o700)
        except OSError:
            pass


def _write(folder: Path, rec: dict[str, Any]) -> Path:
    """One example, written whole (a temporary file made owner-only, then a rename)."""
    _private_dir(folder)
    path = folder / f"{rec['id']}.json"
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, indent=1, ensure_ascii=False, default=str) + "\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# -- what a reader read ------------------------------------------------------------------------------------------------------------------------


def is_read(src: dict[str, Any]) -> bool:
    """Did a reader read this source off a document? The same line the accuracy page draws (src/accuracy.py _source): the client's portal
    answers and answers to the office's questions, a person's entry in review, the firm's details and a rule are not a reader's read; the
    paper questionnaire's scan is (the handwriting and checkbox readers)."""
    import accuracy

    if not src.get("doc_id") or not src.get("doc_type"):
        return False
    return accuracy._source(SimpleNamespace(doc_type=src.get("doc_type"), doc_id=src.get("doc_id")))[0] == "reader"


def _comparable(value: Any) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip().upper()


def _crops(client_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """(document, fact key) -> where the read sat on the page, from the word boxes the questionnaire readers kept (evidence.json): only a scanned
    page read word by word has them."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for doc, entries in (_read(Path(client_dir) / "evidence.json", {}) or {}).items():
        for qid, ev in (entries or {}).items() if isinstance(entries, dict) else ():
            if not isinstance(ev, dict) or ev.get("page") is None or not ev.get("box"):
                continue
            keys = [ev.get("fact_key")] if ev.get("kind") == "choice" else list((ev.get("facts") or {}).values())
            for key in keys:
                if key:
                    out[(doc, key)] = {"page": int(ev["page"]) + 1, "box": [int(v) for v in ev["box"]], "question": qid}
    return out


def _example_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:20]


def _record(case: str, key: str, src: dict[str, Any], final: Any, how: str, by: str, role: str | None, at: str, item: str,
    crops: dict[tuple[str, str], dict[str, Any]], ident: str, outcome: str | None = None, provenance=None) -> dict[str, Any]:
    import correction_provenance
    import version

    doc = str(src.get("doc_id") or "")
    crop = crops.get((doc, key))
    page = src.get("page")
    return {
        "version": VERSION,
        "id": ident,
        "case": case,
        "doc_type": src.get("doc_type"),
        "field": key,
        "document": doc,
        "file": doc.split("#", 1)[0],
        "page": (int(page) + 1) if isinstance(page, int) else (crop or {}).get("page"),
        "read_raw": src.get("raw_value"),
        "read_normalized": src.get("normalized_value"),
        "confidence": src.get("confidence"),
        "final_value": final,
        "outcome": outcome or (KEPT if _comparable(src.get("normalized_value")) == _comparable(final) else CHANGED),
        "how": how,
        "crop": ({"page": crop["page"], "box": crop["box"], "question": crop["question"], "from": BOX_FROM} if crop else None),
        "crop_note": "" if crop else NO_BOX,
        "by": by,
        "role": role,
        "at": at,
        "item": item,
        "undone": None,
        "software": version.VERSION,
        "written": clock.stamp(),
        "observation": correction_provenance.observation(how),
        "source_provenance": provenance or {"state": "legacy_unverified", "reason": "No verified decision-time source binding was available."},
    }


# -- writing: a confirm or a correction on a review card --------------------------------------------------------------------------------------


def from_decision(client_dir: str | Path, stored: dict[str, Any]) -> list[Path]:
    """The examples one decision makes (src/review/state.py record_decision, after the decision is on file): a confirm keeps each read value of
    the item's facts; a correction ("set") replaces the read values of the facts it names. Every reader source of the fact is one example (two
    documents read the same fact: two examples). A fact a rule derived, a fact no reader read, and every other kind of decision make none."""
    client_dir = Path(client_dir)
    action = stored.get("action")
    if action not in ("confirm", "set") or not stored.get("reviewer"):
        return []
    item = stored.get("item") or {}
    facts = (_read(client_dir / "fact_graph.json", {}) or {}).get("facts") or {}  # the readers' own output, never changed by review
    keys = list(item.get("facts") or []) if action == "confirm" else list((stored.get("values") or {}).keys())
    crops: dict[tuple[str, str], dict[str, Any]] | None = None
    written: list[Path] = []
    snapshots = {}
    for key in keys:
        fact = facts.get(key)
        if not isinstance(fact, dict) or fact.get("derived_by"):
            continue
        if action == "confirm":
            if fact.get("status") != "resolved":
                continue  # a confirm signs off only a value that stands (FactGraph.sign_off)
            final = fact.get("value")
        else:
            final = stored["values"][key]
        for src in fact.get("sources") or []:
            if not isinstance(src, dict) or not is_read(src):
                continue
            if crops is None:
                crops = _crops(client_dir)
            ident = _example_id(client_dir.name, item.get("id"), key, src.get("doc_id"), src.get("raw_value"), stored.get("at"))
            while (_read(case_folder(client_dir) / f"{ident}.json", None) or {}).get("undone"):
                ident = _example_id(ident, "again")  # decided, taken back and decided again within the same second: the undone one stays on file
            rec = _record(client_dir.name, key, src, final, "confirm" if action == "confirm" else "correction", stored["reviewer"], stored.get("role"),
                          stored.get("at") or clock.stamp(), str(item.get("id") or ""), crops, ident)
            import correction_provenance
            rec["source_provenance"] = correction_provenance.retain(client_dir, src, rec["at"],
                (stored.get("evidence_confirmation") or {}).get("keys", {}).get(key), snapshots, field_key=key)
            prior = _read(case_folder(client_dir) / f"{ident}.json", None)
            if isinstance(prior, dict) and not prior.get("undone"):
                written.append(case_folder(client_dir) / f"{ident}.json")
                continue  # a historical observation is never enriched from a later graph/source
            written.append(_write(case_folder(client_dir), rec))
    return written


def withdraw(client_dir: str | Path, item_id: str, decided_at: str | None, mark: dict[str, Any]) -> int:
    """A decision taken back (src/review/state.py undo_decision): its examples are marked undone (who, when), kept on file and counted nowhere."""
    folder = case_folder(client_dir)
    n = 0
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        rec = _read(path, None)
        if isinstance(rec, dict) and rec.get("item") == item_id and rec.get("at") == decided_at and not rec.get("undone"):
            rec["undone"] = dict(mark)
            _write(folder, rec)
            n += 1
    return n


# -- writing: the monthly audit, where the firm's hand-filled form differs from a box a reader filled -----------------------------------------------


def from_reference(clients_root: str | Path, ref: Any, result: dict[str, Any], marks: list[dict[str, Any]] | None = None) -> list[Path]:
    """The examples one hand-filled reference makes (src/audit_fill.py audit_forms): each box the office filled with another value than the one a
    reader read (accuracy's "different", from a reader), once per fact, the office's box as the final value. A box marked as the reference's own
    error, a box the office left blank and a box a rule or an answer filled make none. Run again, the same box writes the same example."""
    import accuracy

    client_dir = Path(clients_root) / ref.case
    if not client_dir.is_dir():
        return []
    import client_file
    try:
        client_file.allowed(client_dir)
    except (OSError, ValueError, LookupError):
        return []  # An audit's held graph never authorizes writing into a retired case.
    crops = _crops(client_dir)
    seen: set[str] = set()
    prepared: list[tuple[Path, dict[str, Any]]] = []
    for b in result.get("boxes") or []:
        key = b.get("key") or ""
        final = str(b.get("reference") or "").strip()
        if b.get("kind") != "different" or b.get("cause") != "reader" or not key or not final or not str(b.get("ours") or "").strip() or key in seen:
            continue
        if accuracy.mark_of(marks or [], result.get("form"), b.get("field")):
            continue
        seen.add(key)
        fact = ref.graph.get(key)
        if fact is None or fact.derived_by:
            continue
        for s in fact.sources:
            src = {"doc_id": s.doc_id, "doc_type": s.doc_type, "raw_value": s.raw_value, "normalized_value": s.normalized_value,
                   "confidence": s.confidence, "page": s.page, "extracted_at": s.extracted_at,
                   "evidence_version": s.evidence_version, "instance_id": s.instance_id, "subject_role": s.subject_role, "read_manifest": s.read_manifest}
            if not is_read(src):
                continue
            ident = _example_id(ref.case, "reference", result.get("form"), key, s.doc_id, s.raw_value, final)
            rec = _record(ref.case, key, src, final, "hand_filled_form", FORM_WHO, None, clock.stamp(), f"reference:{result.get('form')}:{b.get('field')}",
                          crops, ident, outcome=CHANGED)  # the office filed another value in the box this read filled
            import correction_provenance
            rec["source_provenance"] = correction_provenance.retain(client_dir, src, rec["at"], field_key=key)
            path = case_folder(client_dir) / f"{ident}.json"
            prepared.append((path, rec))
    if not prepared:
        return []
    import jobs
    from portal.communication_consent import data_gate
    # Match Q1/recorded destruction: installation authority before the case lock.
    # Source preparation stays outside these writer gates; lifecycle is checked
    # again immediately before any existing/new example can be returned/written.
    with data_gate(Path(clients_root).parent), jobs.case_lock(jobs.folder_for(clients_root), client_dir.name, timeout=30):
        try:
            client_file.allowed(client_dir)
        except (OSError, ValueError, LookupError):
            return []
        written: list[Path] = []
        for path, rec in prepared:
            if path.exists():
                written.append(path)
            else:
                written.append(_write(case_folder(client_dir), rec))
    return written


# -- the end of a case ---------------------------------------------------------------------------------------------------------------------


def files_of(client_dir: str | Path) -> list[Path]:
    """The case's example files (the client's file handed over carries them: tools/export_firm.py gather)."""
    folder = case_folder(client_dir)
    return sorted(p for p in folder.glob("*.json") if p.is_file() and not p.is_symlink()) if folder.is_dir() and not folder.is_symlink() else []


def remove_case(client_dir: str | Path) -> int:
    """The case's folder was removed after its keeping date (src/engagement.py mark_destroyed, folder_removed): its examples go with it. Returns
    how many files were removed."""
    folder = case_folder(client_dir)
    if not folder.is_dir() or folder.is_symlink():
        return 0
    n = len(list(folder.glob("*.json")))
    shutil.rmtree(folder)
    return n


# -- reading, always through the gate ------------------------------------------------------------------------------------------------------------


def unprotected(clients_root: str | Path) -> Callable[[str], bool]:
    """The check for a page every member of staff reads (the morning report, docs/accuracy.md): a case that is not protected (src/restricted.py
    is_restricted). A case that cannot be asked is left out, as it is everywhere."""
    import restricted

    seen: dict[str, bool] = {}

    def may(case: str) -> bool:
        if case not in seen:
            try:
                seen[case] = not restricted.is_restricted(Path(clients_root) / case)
            except Exception:  # noqa: BLE001 -- fail closed
                seen[case] = False
        return seen[case]

    return may


def every(clients_root: str | Path, may_open: Callable[[str], bool]) -> list[dict[str, Any]]:
    """Every example in force (not undone) of the cases may_open allows and whose case folder is still there, oldest first. may_open is
    required: an example holds a client's values, so no caller reads one without the gate."""
    base = root(clients_root)
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for folder in sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink()):
        if not (Path(clients_root) / folder.name).is_dir() or not may_open(folder.name):
            continue
        for path in sorted(folder.glob("*.json")):
            rec = _read(path, None)
            if isinstance(rec, dict) and rec.get("field") and rec.get("doc_type") and not rec.get("undone") and rec.get("case") == folder.name:
                out.append(rec)
    out.sort(key=lambda r: (str(r.get("at") or ""), r.get("id") or ""))
    return out


# -- the figures: counts only, never a value or a name -----------------------------------------------------------------------------------------


def reader_name(doc_type: str) -> str:
    import accuracy

    return accuracy._source_names().get(doc_type, str(doc_type or "").replace("_", " ").capitalize())


def field_words(key: str) -> str:
    import events

    return events.words(key)


def rate(changed: int, examples: int) -> str:
    return f"{round(100 * changed / examples)}%" if examples else "no examples"


def figures(examples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Per reader and field: examples, confirmed (the read value kept), changed, the change rate; and the ten fields changed most often (the
    highest share changed, then the most examples). Counts only."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    cases: set[str] = set()
    for e in examples:
        cases.add(e.get("case"))
        r = rows.setdefault((e["doc_type"], e["field"]), {"doc_type": e["doc_type"], "reader": reader_name(e["doc_type"]), "field": e["field"],
                                                         "words": field_words(e["field"]), "examples": 0, "kept": 0, "changed": 0})
        r["examples"] += 1
        r["changed" if e.get("outcome") == CHANGED else "kept"] += 1
    fields = sorted(rows.values(), key=lambda r: (r["reader"], r["words"]))
    for r in fields:
        r["rate"] = r["changed"] / r["examples"]
    worst = sorted((r for r in fields if r["changed"]), key=lambda r: (-r["rate"], -r["examples"], r["reader"], r["words"]))[:WORST]
    return {"examples": sum(r["examples"] for r in fields), "kept": sum(r["kept"] for r in fields), "changed": sum(r["changed"] for r in fields),
            "cases": len(cases), "fields": fields, "worst": worst,
            "observation_strength": "workflow_observation", "independently_adjudicated_error_rate": None,
            "intervention": {"unit": "reader_source_field_observation", "numerator": sum(r["changed"] for r in fields),
                             "denominator": sum(r["examples"] for r in fields),
                             "rate": sum(r["changed"] for r in fields) / sum(r["examples"] for r in fields) if fields else None,
                             "staff_seconds": None,
                             "human_time_input": "tools/evaluate_corpus.py observations.labor: human_observed active_minutes with named observer; no machine runtime substitution"}}


def morning_worst(fig: dict[str, Any]) -> list[dict[str, Any]]:
    """The fields the morning report names: at least MORNING_MIN examples and more than MORNING_RATE of them changed, the worst three."""
    return [r for r in fig["worst"] if r["examples"] >= MORNING_MIN and r["rate"] > MORNING_RATE][:MORNING_WORST]


def _field_line(r: dict[str, Any]) -> str:
    return f"{r['reader']}, {r['words']}: {r['changed']} of {r['examples']} changed ({rate(r['changed'], r['examples'])})"


def nightly(clients_root: str | Path) -> str:
    """The morning report's lines (src/overnight.py reading_night): the count of examples and, when a field has at least MORNING_MIN examples
    with more than MORNING_RATE changed, the worst three. The morning report is one document for every member of staff: protected cases are not
    counted, and it says so. Numbers only."""
    fig = figures(every(clients_root, unprotected(clients_root)))
    if not fig["examples"]:
        return "Reading: no examples yet (an example is kept each time the office confirms or corrects a value a reader read)."
    head = (f"Reading: {fig['examples']} example{'s' if fig['examples'] != 1 else ''} on {len(fig['fields'])} field{'s' if len(fig['fields']) != 1 else ''}, "
            f"{fig['changed']} changed by the office.")
    named = morning_worst(fig)
    lines = [head]
    if named:
        lines.append(f"The fields the office changes most ({MORNING_MIN} or more examples, more than {round(MORNING_RATE * 100)}% changed): "
                     + "; ".join(_field_line(r) for r in named) + ".")
    return "\n".join(lines + [PROTECTED_NOTE])


def render(fig: dict[str, Any] | None) -> list[str]:
    """The section of docs/accuracy.md (src/accuracy.py render_report): counts by reader and field, and the ten fields changed most often."""
    lines = ["## Workflow corrections by document type and field", "",
             "These are workflow observations awaiting independent adjudication, not ground truth or release/training approval. Human staff time is unmeasured.", "",
             "From the firm's own examples: each time a person at the firm confirms a value a reader read from a document, or types a correction "
             "over it, on a review card, or the firm's hand-filled form differs from a box a reader filled, the product keeps one example. A value "
             "the client typed on the portal is not a reader's read and makes none. " + PROTECTED_NOTE, ""]
    if not fig or not fig["examples"]:
        return lines + ["No examples yet.", ""]
    lines += [(f"{fig['examples']} examples on {len(fig['fields'])} fields, from {fig['cases']} case{'s' if fig['cases'] != 1 else ''}: {fig['kept']} confirmed "
              f"as read, {fig['changed']} changed ({rate(fig['changed'], fig['examples'])}). A field with fewer than {MORNING_MIN} examples is an "
              "example, not a rate."), ""]
    lines += ["| Reader | Field | Examples | Confirmed | Changed | Change rate |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['reader']} | {r['words']} | {r['examples']} | {r['kept']} | {r['changed']} | {rate(r['changed'], r['examples'])} |" for r in fig["fields"]]
    lines += ["", f"### The {WORST} fields changed most often", ""]
    if not fig["worst"]:
        return lines + ["The office has changed no read value yet.", ""]
    lines += ["| Reader | Field | Examples | Changed | Change rate |", "|---|---|---|---|---|"]
    lines += [f"| {r['reader']} | {r['words']} | {r['examples']} | {r['changed']} | {rate(r['changed'], r['examples'])} |" for r in fig["worst"]]
    return lines + [""]
