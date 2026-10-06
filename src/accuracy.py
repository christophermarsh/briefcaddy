"""Accuracy against hand-filled references, published with its method (docs/accuracy.md, docs/public/accuracy.md).

A reference is a form a person at the firm filled by hand and filed for a client the pipeline also filled. src/compare.py
compares the two box by box; this module runs it over every reference it finds, says WHY each box that is not identical
differs, and keeps the figures: the report (tools/accuracy_report.py), the night-by-night history the overnight run appends
to (data/accuracy_history.jsonl) and the marks a person makes when the reference itself was wrong
(data/reference/<case>.marks.json).

Where the references are (the firm's own, on the firm's machine; docs/deployment.md):
    data/reference/<case>.pdf          the case's I-485, filled by hand
    data/reference/<case>.<form>.pdf   another form of the same case (n400, i130, i130a ...): the form's id in
                                       schemas/packets/companion_forms.json
    data/reference/<case>.marks.json   what a person marked as the reference's own error (this module writes it)
<case> is the case folder's name in data/clients. The reference is only ever the answer key: it is never an input to a fill.

The causes are worked out from the case, never typed in: for each box the pipeline filled, the fact behind it (through the
form's field map) says whether a document reader, a rule or firm policy, or a typed answer wrote it; for each box it left
blank, whether a question exists for it, whether the fact is held for a person, or whether this filing has no fact for the box.
Nothing here is an estimate: every figure is a count of boxes, with the number it is a count of.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import clock  # noqa: E402
import compare  # noqa: E402
import schema_path

REPO = Path(__file__).resolve().parents[1]

FEW = 10  # fewer references than this behind a figure: the page says it is an example, not a rate
DROP_POINTS = 2.0  # a figure that fell by more than this many points since the last release is flagged for a person

SAMPLES, FIRM = "samples", "firm"
SET_NAMES = {SAMPLES: "the made-up references that ship with the product", FIRM: "the firm's own references"}

# why a box the pipeline filled differs from the reference (the box differs when both are filled and unlike, or the pipeline
# filled a box the reference left blank)
DISAGREE = {
    "reader": "A document reader (a scan, a passport, a notice, the paper questionnaire)",
    "rule": "A rule or a firm policy",
    "answer": "A typed answer (the client's portal, the office's question, a person in review, the firm's details)",
    "reference": "The reference's own error (a person marked it)",
}
# why a box the reference filled is blank on the pipeline's form
SHORT = {"reader": "Read from a document", "rule": "From a rule", "answer": "A typed answer", "reference": "The reference's own error",
         "not_asked": "Not asked or not answered", "no_document": "No document held", "held": "Held for a person",
         "out_of_scope": "Out of scope for the track"}
UNFILLED = {
    "not_asked": "Not asked or not answered (the firm asks the question; the case holds no answer)",
    "no_document": "No document held (a document would supply it; the case has none)",
    "held": "Held for a person (the readers disagree, or a person left it blank)",
    "out_of_scope": "Out of scope for the track (this filing has no answer for the box)",
}

BY_TYPE = ("The boxes the pipeline filled, grouped by where its value came from. A value read from two kinds of document counts once, "
           "under both names together. The share is of the boxes the pipeline filled from that source (identical plus different).")
SOURCE_NAMES = None  # review.learning's names for the readers, loaded on first use (it imports the review package)


def _source_names() -> dict[str, str]:
    global SOURCE_NAMES
    if SOURCE_NAMES is None:
        from review.learning import SOURCE_NAMES as names

        SOURCE_NAMES = dict(names)
    return SOURCE_NAMES


METHOD = [
    "What a reference is: a form a person at the firm filled in by hand and filed for a client. The pipeline fills the same "
    "client's form from the documents and answers on file; the two are compared after the fact. The reference is the answer key "
    "only. It is never an input to a fill, and the pipeline never learns from it.",
    "What is counted: every box on the form that either side filled. A box both left blank is not counted. Every box counts once, "
    "so a value the form repeats (the A-Number printed at the top of each I-485 page, 24 boxes) counts in each place it appears.",
    "Identical: both sides filled the box and wrote the same text, ignoring capitals and extra spaces. Dates are compared as "
    "typed, MM/DD/YYYY: 03/14/2006 and 3/14/2006 are not identical. The pipeline writes names and places without accents "
    "(SAO PAULO, as the firm does), so a reference typed with an accent differs. A Yes/No or multiple-choice question "
    "is one box: the options ticked must be the same, and an unticked question is blank.",
    "Different: both sides filled the box and the text is not the same, or the pipeline filled a box the reference left blank. "
    "Each one is put under the part of the pipeline that wrote the box: a document reader, a rule or firm policy, or a typed "
    "answer. A person who looks at a difference and finds the reference was the one that was wrong marks it in the review app "
    "(Keeping current, Accuracy record, attorney only), with a reason; it is counted as the reference's own error from then on, "
    "with who marked it and when. Nothing is marked by the tool itself.",
    "Unfilled: the reference filled the box and the pipeline left it blank. Each one is put under one reason: the firm asks the "
    "question and the case holds no answer (not asked or not answered); a document would supply it and the case has none (no "
    "document held); the readers disagree or a person left it blank on purpose (held for a person); or this filing has no "
    "answer for the box at all (out of scope for the track).",
    "The percentage: identical boxes as a share of every box either side filled. It is always written with the number of boxes "
    "it is a share of, and each table says how many references stand behind it. A reference is one hand-filled form: a case "
    "with two forms (an I-130 and an I-485) is two references, and where the two counts differ the page says both. With fewer than "
    f"{FEW} references behind a figure it is an example, not a rate, and the page says so.",
    "A disagreement is a question, not a verdict. An example from how this system was built: Item 74 of the I-485 asks whether "
    "the person has been unlawfully present 'since April 1, 1997', a fixed date written into the question itself. A fix made "
    "without the real form changed that answer into a date worked out for each client; checking it against the form on "
    "09/29/2026 showed the original was right and the fix was the mistake (the decision log). That was a wrong fix, not a "
    "comparison with a hand-filled form, but it shows how a correction that looks sensible can be the error, and why a person, "
    "not the tool, decides whose box was wrong.",
    "The references: the made-up references that ship with the product are built by a script from the pipeline's own "
    "demonstration case, with deliberate differences added (typing mistakes, boxes filled by hand that no document supports, "
    "answers left out). They exist so the counting can be tested and shown. They say nothing about how accurate the pipeline "
    "is on a real case. The firm's own figure, on its own hand-filled references, is the one that matters.",
    "The audit of what the office changes: the same comparison, run the other way round, across every case. Once a month, on the firm's own "
    "machine, each case is audited in one of two ways. A case with a hand-filled reference is compared with the reference, box by box. A case "
    "with a Save on a review card (a value changed, filled in or left blank) and no hand-filled I-485 is compared with itself: the I-485 with "
    "every decision applied against the I-485 with the Saves taken out, both filled with the same release; every other decision (an absence mark, "
    "the G-28's confirmation, a name choice) stays on both sides, so only what a person changed on a review card is a difference. A case with "
    "neither (including a case with only an absence mark) is not audited. The reviewers' Saves are also counted every night, from the decision log. "
    "Boxes are the same box across cases when the same fact fills them (the A-Number printed on every page is one box); a change is the same "
    "change when it has the same kind: from NOT APPLICABLE to a name, from a city to another city, from blank to a value, from Yes to No. "
    "A box the attorney marked as the reference's own error is not a change the office made, and a decision taken back, a confirmation or a "
    "decision with no person behind it is not a change. When the same box is changed the same way on 3 or more cases there is one line, in the "
    "morning report and on Reports: a rule may be missing. The lines and the counts are built from the cases the reader may open only: a "
    "protected case is in no row, count or line for someone not named on it (the morning report counts the cases that are not protected, and says "
    "so), and no client's value is on the firm-wide view: the values are inside each case. The share of boxes the office changed is kept in the "
    "history beside the accuracy figure, and a rise of more than 2 points between two audits is said in words. The office's corrections never "
    "change the product by themselves: the audit only reports, a person decides, and a rule change is a release.",
]


# ---------------------------------------------------------------------------------------------- where things live

def data_root(clients_root: Path) -> Path:
    return Path(clients_root).resolve().parent


def reference_dir(clients_root: Path) -> Path:
    """I485_REFERENCE, else data/reference beside the case folders."""
    env = os.environ.get("I485_REFERENCE")
    return Path(env) if env else data_root(clients_root) / "reference"


def history_path(clients_root: Path) -> Path:
    """I485_ACCURACY_HISTORY, else data/accuracy_history.jsonl beside the case folders."""
    env = os.environ.get("I485_ACCURACY_HISTORY")
    return Path(env) if env else data_root(clients_root) / "accuracy_history.jsonl"


def latest_path(clients_root: Path) -> Path:
    """The last night's full result, for the screen (it lists the boxes a person may mark)."""
    return history_path(clients_root).with_name("accuracy_latest.json")


# ---------------------------------------------------------------------------------------------- the forms

FORM_TITLES = {"i485": "Form I-485, Application to Register Permanent Residence or Adjust Status"}


def filing_of(form: str) -> str:
    """The filing whose case a form is read through: the first packet that holds the form (the I-130 is the family
    packet's, the N-400 its own)."""
    import packet

    if form == "i485":
        return "i485"
    for filing, _ in packet.FILINGS.items():
        if form in (packet.load_filing(filing).get("forms") or []):
            return filing
    raise KeyError(f"{form} is not a form in any filing")


def form_title(form: str) -> str:
    if form in FORM_TITLES:
        return FORM_TITLES[form]
    from fill.companion import load_profile

    spec = load_profile()["forms"].get(form)
    return spec["title"] if spec else form.upper()


def _strings(spec: Any):
    if isinstance(spec, str):
        yield spec
    elif isinstance(spec, dict):
        for v in spec.values():
            yield from _strings(v)
    elif isinstance(spec, (list, tuple)):
        for v in spec:
            yield from _strings(v)


def facts_by_box(field_map: dict[str, Any], template: Path) -> dict[str, str]:
    """The fact behind each box, by the box's short name (src/compare.py's): from the form's field map, which names every box
    a fact fills. A question's boxes are known by the group name too (a Yes/No question is one box in the comparison)."""
    from pypdf import PdfReader

    reader = PdfReader(str(template))
    names = set((reader.get_fields() or {}).keys())
    shorts = {compare.short_name(n) for n in names}
    out: dict[str, str] = {}
    for key, spec in field_map.items():
        for text in _strings(spec):
            short = compare.short_name(text) if text in names else text if text in shorts else None  # a map may name a box in full or by its short name
            if short is not None:
                out.setdefault(short, key)
                out.setdefault(compare._WIDGET_INDEX.sub("", short), key)
    return out


def asked_keys() -> set[str]:
    """Every fact a question of the firm's puts to the client or the office: the portal's question banks, the paper
    questionnaire's maps, and each filing's own questions."""
    keys: set[str] = set()
    for path in schema_path.glob("question"):
        bank = json.loads(path.read_text(encoding="utf-8"))
        for section in bank.get("sections", []):
            for q in section.get("questions", []):
                keys |= {s for s in [*_strings(q.get("fact")), *_strings(q.get("facts"))] if "." in s}
    for path in [schema_path.path("paper_map", "map")]:
        for q in json.loads(path.read_text(encoding="utf-8")).get("questions", []):
            if q.get("fact_key"):
                keys.add(q["fact_key"])
    for path in [schema_path.path("paper_map", "text_map")]:
        for q in json.loads(path.read_text(encoding="utf-8")).get("fields", []):
            keys |= {s for s in _strings(q.get("facts")) if "." in s}
    import family
    import filing_questions
    import naturalization

    keys |= {q[0] for q in family.QUESTIONS} | {q[0] for q in naturalization.QUESTIONS}
    for name in filing_questions.MODULES:
        try:
            keys |= {q[0] for q in filing_questions.questions(filing_questions.module(name))}
        except Exception:  # noqa: BLE001 -- a filing with no question list adds nothing here
            continue
    return keys


# ---------------------------------------------------------------------------------------------- a reference, prepared

@dataclass
class Reference:
    """One hand-filled form and the pipeline's own filling of the same form for the same case."""
    case: str
    form: str
    reference_pdf: Path
    ours_pdf: Path
    graph: Any
    box_fact: dict[str, str]
    set: str = FIRM
    track: str = ""
    marks: list[dict[str, Any]] = field(default_factory=list)
    changes: int = 0  # a made-up reference: how many boxes the script changed on purpose


class Skipped(Exception):
    """A reference that can't be compared yet, and why (said in the report, never dropped quietly)."""


def prepare(case_dir: Path, form: str, reference_pdf: Path, workdir: Path, set_name: str = FIRM, track: str = "") -> Reference:
    """The pipeline's filling of `form` for the case in case_dir, with the facts behind it. The I-485 is the file the case
    already holds (every review decision refills it); another form is filled again now from the reviewed case, as the packet does."""
    case = case_dir.name
    if not (case_dir / "fact_graph.json").exists():
        raise Skipped(f"{case}: the case has not been processed")
    from fill import load_field_map

    if form == "i485":
        from review.state import reviewed_graph

        ours = case_dir / "i485_filled.pdf"
        if not ours.exists():
            raise Skipped(f"{case}: the I-485 has not been filled yet")
        graph = reviewed_graph(case_dir)
        template = schema_path.path("template", "i485")
        box_fact = facts_by_box(load_field_map(schema_path.path("field_map", "i485")), template)
    else:
        import packet
        from fill.companion import field_map_for, fill_companions, load_profile

        profile = load_profile()
        if form not in profile["forms"]:
            raise Skipped(f"{case}: a reference for a form the product does not fill.")
        schema = packet.load_filing(filing_of(form))
        graph = packet._case_graph(case_dir, schema)
        spec = profile["forms"][form]
        out = Path(workdir) / case
        out.mkdir(parents=True, exist_ok=True)
        fill_companions(graph, out, profile | {"forms": {form: spec}})
        ours = out / spec["output"]
        box_fact = facts_by_box(field_map_for(spec), schema_path.named(spec["template"]))
    return Reference(case, form, Path(reference_pdf), ours, graph, box_fact, set_name, track or form_title(form))


def find_references(clients_root: Path, ref_dir: Path | None = None) -> tuple[list[tuple[Path, str, Path]], list[str]]:
    """([(case folder, form, the hand-filled PDF)], what was set aside and why): every <case>.pdf and <case>.<form>.pdf in the
    reference folder whose <case> is a case folder here. Other files there (the official forms) are not references."""
    ref_dir = ref_dir or reference_dir(clients_root)
    found, aside = [], []
    if not ref_dir.is_dir():
        return found, aside
    from fill.companion import load_profile

    forms = set(load_profile()["forms"])
    for pdf in sorted(ref_dir.glob("*.pdf")):
        stem, _, form = pdf.stem.partition(".")
        case = Path(clients_root) / stem
        if not case.is_dir():
            continue  # not a case: an official form or instruction
        form = (form or "i485").lower()
        if form != "i485" and form not in forms:
            aside.append(f"{stem}: a reference file named for a form the product does not fill.")
            continue
        found.append((case, form, pdf))
    return found, aside


# ---------------------------------------------------------------------------------------------- one comparison

def _source(s: Any) -> tuple[str, str]:
    """(cause, name) for one source of a fact. The questionnaire is one document type for both the portal (typed by the client:
    an answer) and the paper scan (read by the handwriting and checkbox readers: a reader); the source's file tells them apart."""
    kind, doc = s.doc_type or "", s.doc_id or ""
    if kind == "office_question" or doc == "office question":
        return "answer", "Office questions answered by the client"
    if kind == "firm_profile":
        return "answer", "The firm's own details"
    if kind == "paralegal_review" or doc == "paralegal_review":
        return "answer", "A person's entry in review"
    if kind == "derived" or doc == "fact_graph":
        return "rule", "Rules and firm policies"
    if kind == "intake_questionnaire" and ".pdf" not in doc.lower():
        return "answer", "Client portal answers"
    return "reader", _source_names().get(kind, kind.replace("_", " ").capitalize() or "Unknown")


def provenance(fact: Any, key: str | None = None) -> tuple[str, str]:
    """(cause, name) for a box the pipeline filled: which part wrote it, and the name the document-type table uses (a value
    read from two kinds of source counts once, under both names together). key: the fact the form's map names for the box."""
    if fact is None:
        if key:  # the form was filled when the case still held the fact: it has changed since, and the nightly run refills it
            return "answer", "A value the case's facts no longer hold"
        return "answer", "Filing constants (a value the form is given, such as PRESENT)"
    if fact.derived_by:
        return "rule", "Rules and firm policies"
    review = fact.review
    if review is not None and review.reason.startswith(("set in review", "entered in review")):
        return "answer", "A person's entry in review"
    found = [_source(s) for s in fact.sources]
    if not found:
        return "answer", "Unknown"
    label = " + ".join(sorted({name for _, name in found}))
    causes = {c for c, _ in found}
    return ("reader" if "reader" in causes else "rule" if "rule" in causes else "answer"), label


def unfilled_cause(ref: Reference, short: str, asked: set[str]) -> tuple[str, str | None]:
    key = ref.box_fact.get(short) or ref.box_fact.get(compare._WIDGET_INDEX.sub("", short))
    if key is None:
        return "out_of_scope", None
    fact = ref.graph.get(key)
    if fact is not None and (fact.status == "conflict" or (fact.status == "resolved" and fact.value in (None, ""))):
        return "held", key
    if key in asked:
        return "not_asked", key
    return "no_document", key


def run_one(ref: Reference, asked: set[str]) -> dict[str, Any]:
    """One reference's result: the counts, and every box that is not identical with its cause. The identical boxes are counted
    by where the pipeline's value came from (the document-type table)."""
    rows = [r for r in compare.compare(ref.reference_pdf, ref.ours_pdf) if r.status != "both_blank"]
    if not any(r.reference for r in rows):  # a printed and scanned form has no boxes to read: every box would count as different
        raise Skipped(f"{ref.case} ({form_title(ref.form).split(',')[0]}): the reference has no filled-in boxes (a printed and scanned "
                      "form has none); keep the PDF as the fillable form it was filled in.")
    identical_by_source: dict[str, int] = {}
    boxes = []
    for row in rows:
        short = row.short_name
        key = ref.box_fact.get(short) or ref.box_fact.get(compare._WIDGET_INDEX.sub("", short))
        fact = ref.graph.get(key) if key else None
        if row.status == "match":
            _, label = provenance(fact, key)
            identical_by_source[label] = identical_by_source.get(label, 0) + 1
        elif row.status in ("mismatch", "extra"):
            cause, label = provenance(fact, key)
            boxes.append({"kind": "different", "field": short, "label": row.description, "cause": cause, "source": label, "key": key or "",
                          "reference": row.reference, "ours": row.ours})
        else:
            cause, key = unfilled_cause(ref, short, asked)
            boxes.append({"kind": "unfilled", "field": short, "label": row.description, "cause": cause, "source": "", "key": key or "",
                          "reference": row.reference, "ours": ""})
    return {"case": ref.case, "form": ref.form, "title": form_title(ref.form), "track": ref.track, "set": ref.set,
            "boxes_compared": len(rows), "identical": sum(identical_by_source.values()), "identical_by_source": identical_by_source,
            "boxes": boxes, "marks": ref.marks, "changes": ref.changes}


# ---------------------------------------------------------------------------------------------- marks

def marks_path(ref_dir: Path, case: str) -> Path:
    return Path(ref_dir) / f"{case}.marks.json"


def read_marks(ref_dir: Path, case: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(marks_path(ref_dir, case).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [m for m in data.get("marks", []) if isinstance(m, dict) and m.get("field")]


def add_mark(ref_dir: Path, case: str, form: str, field_name: str, reason: str, by: str, at: str | None = None) -> dict[str, Any]:
    """Records that the reference was wrong on one box: who, when, why. Kept beside the reference, counted from the next
    reading of the figures. One mark per box."""
    reason, by = (reason or "").strip(), (by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every mark records who made it.")
    if not reason:
        raise ValueError("Say why the reference was wrong: the reason is kept with the mark.")
    if len(reason) > 500:
        raise ValueError("Keep the reason under 500 characters.")
    marks = read_marks(ref_dir, case)
    if any(m["form"] == form and m["field"] == field_name for m in marks):
        raise ValueError("That box is already marked.")
    mark = {"form": form, "field": field_name, "reason": reason, "by": by, "at": at or clock.stamp()}
    path = marks_path(ref_dir, case)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"marks": marks + [mark]}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return mark


def mark_of(marks: list[dict[str, Any]], form: str, short: str) -> dict[str, Any] | None:
    return next((m for m in marks if m["form"] == form and m["field"] == short), None)


# ---------------------------------------------------------------------------------------------- the figures

def pct_value(n: int, d: int) -> str:
    """The share as a whole percent, but never 100% unless every box is identical and never 0% unless none is: 1,211 of 1,212 is
    "99.9%" and 3 of 1,000 is "under 1%"."""
    whole = int(100 * n / d + 0.5)
    if n == d:
        return "100%"
    if n == 0:
        return "0%"
    if whole >= 100:
        return "99.9%"
    if whole == 0:
        return "under 1%"
    return f"{whole}%"


def pct_of(n: int, d: int) -> str:
    """'94% of 1,212': a percentage is never written without what it is a share of."""
    return f"{pct_value(n, d)} of {d:,}" if d else "no boxes"


def pct(n: int, d: int) -> str:
    """'94% of 1,212 boxes'."""
    return f"{pct_of(n, d)} box{'es' if d != 1 else ''}" if d else "no boxes"


def share(n: int, d: int) -> float | None:
    return 100.0 * n / d if d else None


def figures(results: list[dict[str, Any]], marks_for=None) -> dict[str, Any]:
    """The counts behind every table, from the results: per form, per document type, in all. marks_for(result) -> the marks to
    apply now (default: the ones the result carries): a marked box is the reference's own error."""
    forms: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    marked = 0
    for r in results:
        marks = marks_for(r) if marks_for else r.get("marks", [])
        f = forms.setdefault(r["form"], {"title": r["title"], "cases": set(), "boxes": 0, "identical": 0, "different": 0, "unfilled": 0,
                                         "causes": {k: 0 for k in DISAGREE}, "unfilled_causes": {k: 0 for k in UNFILLED}})
        f["cases"].add(r["case"])
        f["boxes"] += r["boxes_compared"]
        f["identical"] += r["identical"]
        for label, n in r["identical_by_source"].items():
            s = sources.setdefault(label, {"identical": 0, "different": 0, "cases": set()})
            s["identical"] += n
            s["cases"].add((r["case"], r["form"]))
        for box in r["boxes"]:
            if box["kind"] == "different":
                cause = "reference" if mark_of(marks, r["form"], box["field"]) else box["cause"]
                marked += cause == "reference"
                f["different"] += 1
                f["causes"][cause] += 1
                s = sources.setdefault(box["source"], {"identical": 0, "different": 0, "cases": set()})
                s["different"] += 1
                s["cases"].add((r["case"], r["form"]))
            else:
                f["unfilled"] += 1
                f["unfilled_causes"][box["cause"]] += 1
    cases = {(r["case"]) for r in results}
    for f in forms.values():
        f["references"] = len(f.pop("cases"))
    for s in sources.values():
        s["references"] = len(s.pop("cases"))
    total = {k: sum(f[k] for f in forms.values()) for k in ("boxes", "identical", "different", "unfilled")}
    return {"references": len(results), "cases": len(cases), "forms": forms, "sources": sources, "total": total, "marked": marked,
            "changes": sum(r.get("changes", 0) for r in results)}


def ref_words(references: int, cases: int) -> str:
    """"4 reference forms on 3 cases" where a case holds more than one reference, else "4 references"."""
    if cases and cases != references:
        return f"{references} reference forms on {cases} case{'s' if cases != 1 else ''}"
    return f"{references} reference{'s' if references != 1 else ''}"


# ---------------------------------------------------------------------------------------------- the report

def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _section(title: str, name: str, fig: dict[str, Any], samples: bool) -> list[str]:
    t = fig["total"]
    lines = [f"## {title}", ""]
    if not fig["references"]:
        return lines + ["No references yet.", ""]
    lines += [f"{ref_words(fig['references'], fig['cases'])} ({name}), "
              f"{t['boxes']:,} boxes compared. Identical: {pct(t['identical'], t['boxes'])}. "
              f"Different: {t['different']:,} of {t['boxes']:,} boxes. Unfilled by the pipeline: {t['unfilled']:,} of {t['boxes']:,} boxes."
              + (f" {fig['marked']} of the different boxes were marked as the reference's own error." if fig["marked"] else ""), ""]
    if fig["references"] < FEW:
        lines += [f"Fewer than {FEW} references stand behind these figures: read them as examples, not as rates.", ""]
    lines += ["### By form", ""]
    rows = []
    for fid, f in sorted(fig["forms"].items()):
        rows.append([f"{f['title']}", str(f["references"]), f"{f['boxes']:,}", pct(f["identical"], f["boxes"]),
                     f"{f['different']:,} of {f['boxes']:,}", f"{f['unfilled']:,} of {f['boxes']:,}"])
    lines += _table(["Form", "References", "Boxes compared", "Identical", "Different", "Unfilled"], rows) + [""]
    lines += ["### Why boxes are different, by form", ""]
    rows = [[f["title"], str(f["references"])] + [f"{f['causes'][k]:,} of {f['different']:,}" if f["different"] else "0" for k in DISAGREE]
            for _, f in sorted(fig["forms"].items())]
    lines += _table(["Form", "References"] + [SHORT[k] for k in DISAGREE], rows) + [""]
    lines += ["### Why boxes are unfilled, by form", ""]
    rows = [[f["title"], str(f["references"])] + [f"{f['unfilled_causes'][k]:,} of {f['unfilled']:,}" if f["unfilled"] else "0" for k in UNFILLED]
            for _, f in sorted(fig["forms"].items())]
    lines += _table(["Form", "References"] + [SHORT[k] for k in UNFILLED], rows) + [""]
    lines += ["### By document type", "",
              BY_TYPE, ""]
    rows = []
    for label, s in sorted(fig["sources"].items(), key=lambda kv: (-(kv[1]["identical"] + kv[1]["different"]), kv[0])):
        filled = s["identical"] + s["different"]
        rows.append([label, str(s["references"]), pct(s["identical"], filled), f"{s['different']:,} of {filled:,}"])
    lines += _table(["Where the value came from", "References", "Identical", "Different"], rows) + [""]
    if samples and fig["changes"]:
        lines += [f"Each made-up reference was built from the pipeline's own fill with deliberate differences added by the script: "
                  f"{fig['changes']} in all, across {ref_words(fig['references'], fig['cases'])}. The figures above therefore show that the counting works, "
                  "not how accurate the pipeline is.", ""]
    return lines


def render_report(report: dict[str, Any]) -> str:
    """docs/accuracy.md: the report for the firm's own use, with both sets of references."""
    lines = ["# Accuracy against hand-filled references", "",
             f"Run on {clock.us_date(report['day'])}. Software version {report['version']}. "
             "Written by tools/accuracy_report.py; do not edit it by hand.", "",
             "Every figure below is a count of boxes on a form, written with the number it is a count of, and with the number of "
             "references (hand-filled forms) behind it. The method is at the end of this page.", ""]
    lines += _section("The firm's own references", "the firm's own", report["firm"], False)
    if not report["firm"]["references"]:
        lines += ["The firm has not added any yet. The firm's own figure, on its own hand-filled references, is the one that matters: "
                  "to add one, put the filled PDF in the reference folder under the case's name (see below).", ""]
    if report.get("skipped"):
        lines += ["References set aside:", ""] + [f"- {s}" for s in report["skipped"]] + [""]
    lines += _section("The made-up references that ship with the product", "made up", report["samples"], True)
    import reader_examples

    lines += reader_examples.render(report.get("reading"))  # the firm's labelled examples, per reader and field (brief M1)
    lines += ["## How to add the firm's own references", "",
              "Put the form a person filled by hand, as a PDF, in the reference folder (data/reference) under the name of the case's folder "
              "in data/clients: <case>.pdf for the I-485, <case>.<form>.pdf for another form (n400, i130, i130a). The case must be "
              "processed. The next overnight run, or `python tools/accuracy_report.py`, compares it and adds it to these figures. "
              "A reference is never read by anything that fills a form.", "",
              "## How the figures are counted", ""]
    lines += [f"{i}. {m}" for i, m in enumerate(METHOD, 1)] + [""]
    return "\n".join(lines)


def render_public(report: dict[str, Any]) -> str:
    """docs/public/accuracy.md: the sales page. The same figures, from the made-up references, and nothing the report does not say."""
    fig = report["samples"]
    t = fig["total"]
    lines = ["# How accurate is the filling, and how we show you", "",
             f"Figures as of {clock.us_date(report['day'])}, software version {report['version']}.", "",
             "We publish how we count accuracy, and give you the tool to run the same count on your own filings. This page is "
             "regenerated by hand when a release changes how forms are filled, and the date and version above say when.", "",
             "## What we measure", "",
             "Your paralegals have filed forms by hand. For a client whose form was filled by hand, the pipeline fills the same form from "
             "the documents and the answers on file. A box-by-box comparison says, for every box: identical, different (and why), or left "
             "blank by the pipeline (and why). The hand-filled form is only the answer key: nothing learns from it and nothing fills "
             "from it.", "",
             "## The figures we can show you today", "",
             f"These come from {ref_words(fig['references'], fig['cases'])} made up and shipped with the product: {t['boxes']:,} boxes "
             f"compared, {pct(t['identical'], t['boxes'])} identical.", ""]
    if fig["references"] < FEW:
        lines += [f"Fewer than {FEW} references stand behind them, so they are examples, not rates.", ""]
    lines += ["The made-up references are built by a script from our demonstration case with deliberate differences added. They show "
              "that the counting works and what the report looks like. They are not a measure of accuracy on a real case.", "",
              "### By form", ""]
    rows = [[f["title"], str(f["references"]), f"{f['boxes']:,}", pct(f["identical"], f["boxes"]), f"{f['different']:,} of {f['boxes']:,}",
             f"{f['unfilled']:,} of {f['boxes']:,}"] for _, f in sorted(fig["forms"].items())]
    lines += _table(["Form", "References", "Boxes compared", "Identical", "Different", "Unfilled"], rows) + [""]
    lines += ["### Why boxes are different or unfilled", ""]
    rows = [[DISAGREE[k], f"{sum(f['causes'][k] for f in fig['forms'].values()):,} of {t['different']:,}"] for k in DISAGREE]
    rows += [[UNFILLED[k], f"{sum(f['unfilled_causes'][k] for f in fig['forms'].values()):,} of {t['unfilled']:,}"] for k in UNFILLED]
    lines += _table(["Cause", "Boxes"], rows) + [""]
    lines += ["### By document type", "", BY_TYPE, ""]
    rows = []
    for label, s in sorted(fig["sources"].items(), key=lambda kv: (-(kv[1]["identical"] + kv[1]["different"]), kv[0])):
        filled = s["identical"] + s["different"]
        rows.append([label, str(s["references"]), pct(s["identical"], filled), f"{s['different']:,} of {filled:,}"])
    lines += _table(["Where the value came from", "References", "Identical", "Different"], rows) + [""]
    lines += ["## The number that matters is yours", "",
              "The firm's own figure on its own references is the one that matters. Put the forms your people filled by hand in the "
              "reference folder and the overnight run adds them to the report every night. Your screen then shows, in words, how the "
              "figure has moved over the last 30 nights, and says so plainly when it falls after an update so that a person looks.", "",
              "## How the figures are counted", ""]
    lines += [f"{i}. {m}" for i, m in enumerate(METHOD, 1)] + [""]
    return "\n".join(lines)


_PCT_OF = re.compile(r"\d+(?:\.\d+)?% of [\d,]+ box(?:es)?")
# every way a share can be written: 94%, 94 %, 94 percent, "19 in 20", "1 out of 4"
_SHARE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|percent\b|per cent\b)|\b\d+\s+(?:in|out of)\s+\d+\b", re.I)


def public_problems(public: str, report: str) -> list[str]:
    """What is wrong with the public page: every share on it (a percentage in figures or words, "19 in 20") must be written as
    "N% of M boxes", and the same percentage of the same number of boxes must stand in the report. An empty list: nothing on the
    page is overstated or unexplained."""
    in_report = set(_PCT_OF.findall(report))
    problems = []
    for m in _SHARE.finditer(public):
        around = public[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
        both = _PCT_OF.match(public, m.start())
        if both is None:
            problems.append(f"a share not written as a percentage of a number of boxes: ...{around}...")
        elif both.group(0) not in in_report:
            problems.append(f"'{both.group(0)}' is not in the report: ...{around}...")
    return problems


# ---------------------------------------------------------------------------------------------- running it

def compare_all(refs: list[Reference]) -> tuple[list[dict[str, Any]], list[str]]:
    """([the results], what was set aside and why): one reference that can't be compared (a truncated or empty file, a printed and
    scanned form with no boxes) is said so and left out, and the rest of the night runs."""
    asked = asked_keys()
    results, aside = [], []
    for r in refs:
        try:
            results.append(run_one(r, asked))
        except Skipped as exc:
            aside.append(str(exc))
        except Exception:  # noqa: BLE001 -- a file that is not a form must not cost the night its figure
            aside.append(f"{r.case} ({form_title(r.form).split(',')[0]}): the reference file could not be opened as a form.")
    return results, aside


def firm_references(clients_root: Path, workdir: Path, ref_dir: Path | None = None) -> tuple[list[Reference], list[str]]:
    ref_dir = ref_dir or reference_dir(clients_root)
    found, aside = find_references(clients_root, ref_dir)
    refs = []
    for case_dir, form, pdf in found:
        try:
            ref = prepare(case_dir, form, pdf, workdir)
        except Skipped as exc:
            aside.append(str(exc))
            continue
        except Exception:  # noqa: BLE001 -- one reference that can't be read must not stop the others
            aside.append(f"{case_dir.name} ({form_title(form).split(',')[0]}): the reference could not be compared.")
            continue
        ref.marks = read_marks(ref_dir, case_dir.name)
        refs.append(ref)
    return refs, aside


def run(clients_root: Path, workdir: Path, samples: bool = True, firm: bool = True, ref_dir: Path | None = None) -> dict[str, Any]:
    """Every reference compared. samples / firm choose which sets are run; the result carries both sets' figures (an empty one
    for a set not run) and the results behind them."""
    import version

    refs, skipped = firm_references(clients_root, workdir / "firm", ref_dir) if firm else ([], [])
    firm_results, aside = compare_all(refs)
    skipped = skipped + aside
    sample_results: list[dict[str, Any]] = []
    if samples:
        import accuracy_samples

        sample_results = compare_all(accuracy_samples.build(workdir / "samples"))[0]
    for r in firm_results:
        r["set"] = FIRM
    rdir = ref_dir or reference_dir(clients_root)
    return {"at": clock.stamp(), "day": clock.day(clock.stamp()), "version": version.VERSION, "skipped": skipped,
            "results": {FIRM: firm_results, SAMPLES: sample_results},
            "firm": figures(firm_results, lambda r: read_marks(rdir, r["case"])), "samples": figures(sample_results),
            "reading": _reading(clients_root) if firm else None}


def _reading(clients_root: Path) -> dict[str, Any] | None:
    """The firm's labelled examples counted per reader and field (src/reader_examples.py): the cases that are not protected, as on every page all
    staff read."""
    import reader_examples

    try:
        return reader_examples.figures(reader_examples.every(clients_root, reader_examples.unprotected(clients_root)))
    except Exception:  # noqa: BLE001 -- the page says no examples rather than fail the report
        return None


# ---------------------------------------------------------------------------------------------- the nights' history

def history_record(report: dict[str, Any], chosen: str, audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """One night's line in the history: the figures of the set it measured, never the boxes. audit: the counts of the last audit of what the office
    changes (src/audit_fill.py counts), so a drift in it shows beside the accuracy figure."""
    fig = report[chosen]
    t = fig["total"]
    return {"at": report["at"], "day": report["day"], "version": report["version"], "set": chosen, "references": fig["references"], "cases": fig["cases"],
            "boxes": t["boxes"], "identical": t["identical"], "different": t["different"], "unfilled": t["unfilled"],
            "forms": {fid: {"title": f["title"], "references": f["references"], "boxes": f["boxes"], "identical": f["identical"]}
                      for fid, f in fig["forms"].items()},
            **({"audit": audit} if audit else {})}


def audit_counts(clients_root: Path) -> dict[str, Any] | None:
    """The audit's counts for the history: what the last audit of the office's changes compared and found (None before one has run)."""
    import audit_fill

    return audit_fill.counts(audit_fill.read(clients_root))


def _audits(history: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str]]:
    """(the audit's day, its counts, the software version when the night recorded it), one per audit, oldest first."""
    seen: dict[str, tuple[dict[str, Any], str]] = {}
    for rec in history:
        a = rec.get("audit")
        if isinstance(a, dict) and a.get("forms_day") and a.get("boxes"):  # an audit that compared no boxes says nothing
            seen[a["forms_day"]] = (a, rec.get("version") or "")
    return [(day, a, ver) for day, (a, ver) in sorted(seen.items())]


def audit_line(history: list[dict[str, Any]]) -> str:
    """The last audit of what the office changes, in words, with what it is a share of, and the one before it so a drift shows."""
    from audit_fill import THRESHOLD

    runs = _audits(history)
    if not runs:
        return ""
    day, a, _ = runs[-1]
    line = f"Boxes the office changed {pct_of(a['changed'], a['boxes'])} on {clock.us_date(day)}"
    if len(runs) > 1:
        day0, a0, _ = runs[-2]
        line += f", {pct_of(a0['changed'], a0['boxes'])} on {clock.us_date(day0)}"
    return line + f", on {a['forms']} form{'s' if a['forms'] != 1 else ''} the office filled or corrected; " + (
        f"{a['groups']} box{'es' if a['groups'] != 1 else ''} changed the same way on {THRESHOLD} or more cases." if a.get("groups")
        else f"no box changed the same way on {THRESHOLD} or more cases.")


def audit_rises(history: list[dict[str, Any]]) -> list[str]:
    """A plain sentence when the share of boxes the office changed rose by more than DROP_POINTS between two audits: a person should look."""
    runs = _audits(history)
    if len(runs) < 2:
        return []
    (_, old, _), (_, new, ver) = runs[-2], runs[-1]
    a, b = share(old["changed"], old["boxes"]), share(new["changed"], new["boxes"])
    if a is None or b is None or b - a <= DROP_POINTS:
        return []
    return [f"Boxes the office changed rose from {pct_of(old['changed'], old['boxes'])} to {pct_of(new['changed'], new['boxes'])}"
            + (f" after the {ver} update" if ver else "") + ": a person should look."]


def append_history(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lead = ""
    try:
        with open(path, "rb") as fh:  # a last line cut short (a crash mid-write) must not swallow tonight's
            fh.seek(0, 2)
            if fh.tell():
                fh.seek(-1, 2)
                lead = "" if fh.read(1) == b"\n" else "\n"
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(lead + json.dumps(record, ensure_ascii=False) + "\n")


def read_history(path: Path) -> list[dict[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("day") and rec.get("boxes") is not None:
            out.append(rec)
    return sorted(out, key=lambda r: clock.key(r.get("at") or r["day"]))


def nights(history: list[dict[str, Any]], last: int = 30) -> list[dict[str, Any]]:
    """One record per night (the last of the day), the last `last` nights, oldest first."""
    by_day: dict[str, dict[str, Any]] = {}
    for rec in history:
        by_day[rec["day"]] = rec
    return [by_day[d] for d in sorted(by_day)][-last:]


def _p(rec: dict[str, Any]) -> float | None:
    return share(rec["identical"], rec["boxes"])


def history_line(history: list[dict[str, Any]]) -> dict[str, Any]:
    """The last 30 nights in words: {"line", "nights": [{"day", "text"}], "set"}. The line names the latest night's figure with
    what it is a share of, and the figure a week before when the history reaches back that far (each with its own count)."""
    rows = nights(history)
    if not rows:
        return {"line": "", "nights": [], "set": None}
    latest = rows[-1]
    kind = "the made-up references" if latest.get("set") == SAMPLES else "the firm's own references"
    today = clock.local_date(latest["day"])
    line = f"Identical boxes {pct_of(latest['identical'], latest['boxes'])} on {clock.us_date(latest['day'])}"
    week = [r for r in rows[:-1] if (today - clock.local_date(r["day"])).days >= 7 and r.get("set") == latest.get("set")]
    if week:
        w = week[-1]
        when = "a week ago" if (today - clock.local_date(w["day"])).days < 10 else "on " + clock.us_date(w["day"])
        line += f", {pct_of(w['identical'], w['boxes'])} {when}"
    line += f", on {ref_words(latest['references'], latest.get('cases', latest['references']))} ({kind})."
    return {"line": line, "set": latest.get("set"),
            "nights": [{"day": r["day"], "text": f"{clock.us_date(r['day'])[:5]}: {pct_of(r['identical'], r['boxes'])}"} for r in rows]}


def drops(history: list[dict[str, Any]]) -> list[str]:
    """A plain sentence for each figure that fell by more than DROP_POINTS since the last release: the latest night against
    the last night of the release before it (the same set of references). A person should look."""
    if not history:
        return []
    latest = history[-1]
    before = [r for r in history if r.get("version") != latest.get("version") and r.get("set") == latest.get("set")]
    if not before or latest.get("set") is None:
        return []
    old = before[-1]
    out = []

    def fell(name: str, was: dict[str, Any], now: dict[str, Any]) -> None:
        a, b = share(was["identical"], was["boxes"]), share(now["identical"], now["boxes"])
        if a is not None and b is not None and a - b > DROP_POINTS:
            out.append(f"{name} fell from {pct_of(was['identical'], was['boxes'])} to {pct_of(now['identical'], now['boxes'])} "
                       f"after the {latest['version']} update: a person should look.")

    fell("Identical boxes", old, latest)
    for fid, f in (latest.get("forms") or {}).items():
        if fid in (old.get("forms") or {}):
            fell(f"Identical boxes on {f['title'].split(',')[0]}", old["forms"][fid], f)
    return out


def chosen_set(report: dict[str, Any]) -> str:
    """The figures the history follows: the firm's own references when it has any, else the made-up ones."""
    return FIRM if report["firm"]["references"] else SAMPLES


def nightly(clients_root: Path) -> str:
    """The overnight run's step: compare, append the figures to the history, keep the full result for the screen. The made-up
    references are run only while the firm has none of its own that can be compared. Returns the line for the run's log."""
    import tempfile

    own, _ = find_references(clients_root)
    with tempfile.TemporaryDirectory() as tmp:
        report = run(clients_root, Path(tmp), samples=not own, firm=bool(own))
        if not report["firm"]["references"]:  # none of the firm's own could be compared: the made-up ones stand in
            report = run(clients_root, Path(tmp), samples=True, firm=bool(own))
    chosen = chosen_set(report)
    append_history(history_path(clients_root), history_record(report, chosen, audit_counts(clients_root)))
    path = latest_path(clients_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = path.with_suffix(f".{os.getpid()}.tmp")
    tmp_file.write_text(json.dumps({"at": report["at"], "day": report["day"], "version": report["version"], "set": chosen,
                                    "skipped": report["skipped"], "results": report["results"][chosen]}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_file, path)
    t = report[chosen]["total"]
    return (f"Accuracy: {pct(t['identical'], t['boxes'])} identical on "
            f"{ref_words(report[chosen]['references'], report[chosen]['cases'])} ({SET_NAMES[chosen]}).")


def latest(clients_root: Path) -> dict[str, Any] | None:
    try:
        return json.loads(latest_path(clients_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def shown(value: str) -> str:
    """A box's value as a person reads it: a ticked Yes or No in words, a blank box said to be blank, never a form code."""
    if value == "":
        return "Left blank"
    if value.startswith("/"):
        return {"/Y": "Yes", "/N": "No"}.get(value, "Option " + " and ".join(v.lstrip("/") for v in value.split()))
    return value


def screen(clients_root: Path, may_open=None) -> dict[str, Any]:
    """What the Accuracy record screen shows about the references: the history in words, a sentence for any fall, the latest
    figures (the marks counted as they stand now) and the boxes that differ, each markable. may_open(case): the cases the reader may
    see; the others are counted but not listed."""
    hist = read_history(history_path(clients_root))
    last = latest(clients_root)
    out: dict[str, Any] = {"history": history_line(hist), "drops": drops(hist) + audit_rises(hist), "audit": audit_line(hist), "latest": None, "different": [],
                           "hidden": 0, "can_mark": False}
    if last is None:
        return out
    rdir = reference_dir(clients_root)
    firm = last["set"] == FIRM
    fig = figures(last["results"], (lambda r: read_marks(rdir, r["case"])) if firm else None)
    out["latest"] = {"day": last["day"], "version": last["version"], "set": last["set"], "set_name": SET_NAMES[last["set"]], "figures": _plain(fig),
                     "skipped": last.get("skipped", [])}
    out["can_mark"] = firm
    for r in last["results"]:
        if firm and may_open is not None and not may_open(r["case"]):  # the made-up references are nobody's case
            out["hidden"] += sum(1 for b in r["boxes"] if b["kind"] == "different")
            continue
        marks = read_marks(rdir, r["case"]) if firm else r.get("marks", [])
        for b in r["boxes"]:
            if b["kind"] != "different":
                continue
            m = mark_of(marks, r["form"], b["field"])
            out["different"].append({"case": r["case"], "form": r["form"], "title": r["title"], "field": b["field"], "label": b["label"],
                                     "key": b["key"], "reference": shown(b["reference"]), "ours": shown(b["ours"]), "cause": "reference" if m else b["cause"],
                                     "cause_name": DISAGREE["reference" if m else b["cause"]], "source": b["source"],
                                     "mark": m})
    out["different"].sort(key=lambda d: (d["mark"] is not None, d["case"], d["form"], d["field"]))
    return out


def _plain(fig: dict[str, Any]) -> dict[str, Any]:
    return {"references": fig["references"], "words": ref_words(fig["references"], fig["cases"]), "total": fig["total"], "marked": fig["marked"], "pct": pct(fig["total"]["identical"], fig["total"]["boxes"]),
            "few": fig["references"] < FEW,
            "forms": [{"title": f["title"], "references": f["references"], "boxes": f["boxes"], "identical": f["identical"], "different": f["different"],
                       "unfilled": f["unfilled"], "pct": pct(f["identical"], f["boxes"])} for _, f in sorted(fig["forms"].items())]}


def mark(clients_root: Path, case: str, form: str, field_name: str, reason: str, by: str) -> dict[str, Any]:
    """A person's mark that the reference was wrong on one box. Only a box the last run found different, in the firm's own
    references, can be marked."""
    last = latest(clients_root)
    if last is None or last["set"] != FIRM:
        raise ValueError("There are no references of the firm's own to mark.")
    result = next((r for r in last["results"] if r["case"] == case and r["form"] == form), None)
    if result is None or not any(b["kind"] == "different" and b["field"] == field_name for b in result["boxes"]):
        raise ValueError("That box is not one the last comparison found different.")
    return add_mark(reference_dir(clients_root), case, form, field_name, reason, by)
