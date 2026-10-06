"""The loop that finds the next wrong box: what the office changes, box by box, across every case.

Two sources, one grouping. Nothing here changes how a form is filled: it reports, a person decides, and a rule change is a release.

  1. The forms. For every case that holds a hand-filled reference (the accuracy tool's own convention: src/accuracy.py, data/reference) or
     whose filled I-485 the office corrected on screen, the product's fill against it box by box with the comparison engine (src/compare.py).
     For a case the office corrected, the product's fill is the form the case's own facts and rules give with no decision applied
     (review.state.reviewed_graph(decisions={})), against the form with every decision applied: both are filled now with this release,
     so a difference is the office's doing and never an older release's.
  2. The reviewers' Saves. The decision log's Saves (a value changed on a review card, or left blank), each against what the product held
     before the person decided (review.learning.outcomes).

Each change is a row: the case, the box, the kind of change ("from NOT APPLICABLE to a name", "from a city to another city", "from blank to a
value"), and the two values. Rows are grouped by form, box and kind of change across cases. When one group reaches THRESHOLD cases there is one
line for the morning report and for Reports: "The office changes Part 1, Item 2 from NOT APPLICABLE to a name on 4 cases: a rule may be
missing". The line is for a person. A box the attorney marked as the reference's own error is not a change the office made. A change made by
the product itself is not here: every row has a person behind it, or a hand-filled form.

The catalog is data/audit_fill.json (I485_AUDIT_FILL points elsewhere): it holds each case's changed boxes with their values, so it is a
person's data like the case folders, owner-only (0600) and never in the export of the firm's data (src/records.py). Written by tools/fill_audit.py
(the forms, monthly, on the firm's machine) and the overnight run (the Saves, every night). A reader of Reports sees counts and the cases they may
open: never a value, and a protected case they may not open is in no row, count or line for them. The values are shown only inside the case.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import accuracy  # noqa: E402
import clock  # noqa: E402
import compare  # noqa: E402
import events  # noqa: E402
import schema_path

REPO = Path(__file__).resolve().parents[1]

FILE = "audit_fill.json"
VERSION = 1
THRESHOLD = 3  # the same box changed the same way on this many cases: a line for a person
TOP_LINES = 5  # the morning report names this many; the rest are on Reports
FORM, SAVE = "form", "save"
SEEN = {FORM: "Filed and corrected forms", SAVE: "Reviewers' Saves"}
SEEN_BOTH = "Filed and corrected forms, and reviewers' Saves"
NOT_APPLICABLE = {"N/A", "NA", "NONE", "NOT APPLICABLE"}
CHANGES = ("set", "blank")  # a decision that changes a value (confirm and acknowledge do not)
WHO = "The audit of the forms"  # the ledger's name for the monthly run when no person is named

# ---------------------------------------------------------------------------------------------- where


def path(clients_root: Path) -> Path:
    """I485_AUDIT_FILL, else data/audit_fill.json beside the case folders."""
    env = os.environ.get("I485_AUDIT_FILL")
    return Path(env) if env else accuracy.data_root(clients_root) / FILE


# ---------------------------------------------------------------------------------------------- the kind of change in words

_NAMES = {"name", "names", "given", "family", "middle", "surname"}
# (words in the box's name, what it holds): the first row that matches decides
_NOUNS = [({"city", "town"}, "a city"), ({"state"}, "a state"), ({"zip", "postal"}, "a ZIP code"), ({"street"}, "a street"),
          ({"province"}, "a province"), ({"country", "nationality", "citizenship"}, "a country"), ({"apt", "unit", "ste", "flr"}, "an apartment number"),
          (_NAMES, "a name"), ({"date", "dob", "since", "birth"}, "a date"), ({"phone", "mobile", "telephone"}, "a phone number"),
          ({"number", "ssn", "alien", "receipt", "passport"}, "a number")]
_DATE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})$")


def _tokens(ident: str) -> set[str]:
    """The words in a fact key or a box's name: applicant.birth_city and Pt1Line7_CityTownOfBirth[0] both hold "city"."""
    return {t.lower() for t in re.findall(r"[A-Z][a-z]+|[a-z]+", str(ident).replace("_", " "))}


def noun_of(ident: str, before: str, after: str) -> str:
    """What a box holds, in two words ("a name", "a city"): from the box's own name, else from the shape of the values."""
    words = _tokens(ident)
    for keys, noun in _NOUNS:
        if words & keys:
            return noun
    sample = next((v for v in (after, before) if v and v.upper() not in NOT_APPLICABLE), "")
    if _DATE.match(sample):
        return "a date"
    if sample.isdigit():
        return "a number"
    return "a value"


def _blank(value: Any) -> bool:
    return not str(value if value is not None else "").strip()


def kind_of(before: Any, after: Any) -> str:
    """How a value changed: na_to_value, value_to_na, blank_to_value, value_to_blank, yes_to_no, no_to_yes or value_to_value."""
    b, a = compare.normalize(str(before or "")), compare.normalize(str(after or ""))
    if not b:
        return "blank_to_value"
    if not a:
        return "value_to_blank"
    if b in NOT_APPLICABLE:
        return "na_to_value"
    if a in NOT_APPLICABLE:
        return "value_to_na"
    yes, no = {"/Y", "YES"}, {"/N", "NO"}
    if b in yes and a in no:
        return "yes_to_no"
    if b in no and a in yes:
        return "no_to_yes"
    return "value_to_value"


def change_words(kind: str, noun: str) -> str:
    """"from NOT APPLICABLE to a name", "from a city to another city", "from blank to a value": no value of any client in it."""
    other = re.sub(r"^an? ", "", noun)
    return {"na_to_value": f"from NOT APPLICABLE to {noun}", "value_to_na": f"from {noun} to NOT APPLICABLE", "blank_to_value": f"from blank to {noun}",
            "value_to_blank": f"from {noun} to blank", "yes_to_no": "from Yes to No", "no_to_yes": "from No to Yes",
            "value_to_value": "from one value to another" if noun == "a value" else f"from {noun} to another {other}"}[kind]


# ---------------------------------------------------------------------------------------------- naming a box

class Boxes:
    """A box in the product's own words: the I-485's Part and Item and the question, from the form's tooltips (review.state.Catalog); another
    form's Part and question. Loaded on first use (reading a template's fields takes a moment)."""

    def __init__(self, catalog=None) -> None:
        self._catalog = catalog  # the review app's own, when it has one
        self._companions: dict[str, tuple[str, dict]] | None = None
        self._tips: dict[str, dict[str, str]] = {}
        self._named: dict[tuple[str, str, str], tuple[str, str]] = {}

    @property
    def catalog(self):
        if self._catalog is None:
            from fill import load_field_map
            from review.state import Catalog

            self._catalog = Catalog(load_field_map(schema_path.path("field_map", "i485")), schema_path.path("template", "i485"))
        return self._catalog

    def _companion_forms(self) -> dict[str, tuple[str, dict]]:
        """fact key -> (form id, the form's entry in schemas/packets/companion_forms.json), the first form whose map holds the key."""
        if self._companions is None:
            from fill.companion import load_profile

            out: dict[str, tuple[str, dict]] = {}
            for fid, spec in load_profile()["forms"].items():
                for key in spec.get("map") or {}:
                    out.setdefault(key, (fid, spec))
            self._companions = out
        return self._companions

    def form_of(self, fact_key: str) -> str:
        """The form a fact fills a box on: the I-485, else the first other form; "" for an answer no form holds as a box (the forms are built from it)."""
        if fact_key in self.catalog.field_map:
            return "i485"
        return self._companion_forms().get(fact_key, ("", {}))[0]

    def tooltips(self, form: str) -> dict[str, str]:
        if form not in self._tips:
            from fill.companion import load_profile
            from pypdf import PdfReader
            from review.state import tidy_tooltip

            spec = load_profile()["forms"][form]
            fields = PdfReader(str(schema_path.named(spec["template"]))).get_fields() or {}
            self._tips[form] = {compare.short_name(n): tidy_tooltip(str(f.get("/TU") or "")) for n, f in fields.items()}
        return self._tips[form]

    def name(self, form: str, key: str, field: str, tip: str = "") -> tuple[str, str]:
        """(the Part and Item or Part, the question) of a box; either may be "" for a box the form does not number."""
        memo = (form, key, field)
        if memo in self._named:
            return self._named[memo]
        from review.state import concise, i485_ref, short_label, tidy_tooltip

        if form == "i485":
            if key:
                ref, label = self.catalog.ref(key), short_label(key, concise(self.catalog.label(key), 90))
            else:
                tip = tidy_tooltip(tip)
                ref, label = i485_ref(tip), concise(tip, 90)
        elif form:
            if not tip and field:
                tip = self.tooltips(form).get(compare.short_name(field), "")
            part = re.match(r"Part \d+", tip or "")
            short = accuracy.form_title(form).split(",")[0]
            ref = f"{short}, {part.group(0)}" if part else short
            label = concise(tip, 90) if tip else (key.rsplit(".", 1)[-1].replace("_", " ").capitalize() if key else field)
        else:  # an answer the forms are built from
            ref, label = "", short_label(key, concise(self.catalog.label(key), 90))
        self._named[memo] = (ref, label)
        return ref, label


# ---------------------------------------------------------------------------------------------- rows

def _row(case: str, source: str, form: str, ident: str, ref: str, label: str, before: Any, after: Any, **extra: Any) -> dict[str, Any]:
    before, after = "" if before is None else str(before), "" if after is None else str(after)
    noun = noun_of(ident, before, after)
    kind = kind_of(before, after)
    return {"case": case, "source": source, "form": form, "ident": ident, "ref": ref, "label": label, "kind": kind, "noun": noun,
            "before": accuracy.shown(before) if before.startswith("/") else before, "after": accuracy.shown(after) if after.startswith("/") else after, **extra}


def _ident(key: str, field: str) -> str:
    """What makes two boxes the same box across cases: the fact behind it; else the box's name without its widget number (the A-Number
    printed at the top of each I-485 page is one box in 24 places)."""
    if key:
        return key
    return compare._WIDGET_INDEX.sub("", re.sub(r"^Pt1Line4_AlienNumber\[\d+\]$", "Pt1Line4_AlienNumber", field))


def rows_from_result(result: dict[str, Any], boxes: Boxes, marks: list[dict[str, Any]], basis: str) -> list[dict[str, Any]]:
    """One row per box the office filled or changed against the product's fill, from an accuracy comparison (src/accuracy.py run_one): the
    reference is the office's, the product's value is the one before. A box marked as the reference's own error is left out."""
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for b in result["boxes"]:
        if accuracy.mark_of(marks, result["form"], b["field"]):
            continue
        ident = _ident(b["key"], b["field"])
        ref, label = boxes.name(result["form"], b["key"], b["field"], b["label"])
        row = _row(result["case"], FORM, result["form"], ident, ref, label, b["ours"], b["reference"], basis=basis)
        out.setdefault((result["case"], result["form"], ident + "|" + row["kind"]), row)  # one row for a box the form repeats
    return list(out.values())


def save_rows(client_dir: Path, boxes: Boxes) -> list[dict[str, Any]]:
    """The case's Saves: each value a person changed (or filled, or left blank) on a review card against what the product held, from the decision log
    (review.learning.outcomes). Nothing a decision undone, a confirmation, an acknowledgement or a declaration's wording."""
    from review.learning import outcomes

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in outcomes(client_dir):
        if r["outcome"] not in ("corrected", "filled", "blanked") or not r.get("reviewer") or r.get("kind") in KEPT_KINDS:
            continue  # a change has a person behind it
        form = boxes.form_of(r["key"])
        ref, label = boxes.name(form, r["key"], "")
        row = _row(client_dir.name, SAVE, form, r["key"], ref, label, r["before"], r["after"], reason=str(r.get("note") or ""), by=str(r["reviewer"]), at=r.get("at") or "")
        out[(r["key"], row["kind"])] = row
    return list(out.values())


# ---------------------------------------------------------------------------------------------- the groups

def group_id(form: str, ident: str, kind: str) -> str:
    return hashlib.sha1(f"{form}|{ident}|{kind}".encode()).hexdigest()[:10]


def groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows grouped by form, box and kind of change: {id, form, ref, label, kind, noun, words, cases (ids, sorted), seen (the sources), line}.
    Most cases first. No value is in a group."""
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = by.setdefault(group_id(r["form"], r["ident"], r["kind"]), {
            "id": group_id(r["form"], r["ident"], r["kind"]), "form": r["form"], "ref": r["ref"], "label": r["label"], "kind": r["kind"], "noun": r["noun"],
            "words": change_words(r["kind"], r["noun"]), "cases": set(), "seen": set()})
        g["cases"].add(r["case"])
        g["seen"].add(r["source"])
    out = []
    for g in by.values():
        g["cases"] = sorted(g["cases"])
        g["seen"] = [s for s in (FORM, SAVE) if s in g["seen"]]
        g["line"] = line_of(g)
        out.append(g)
    return sorted(out, key=lambda g: (-len(g["cases"]), g["ref"], g["label"], g["words"]))


def line_of(g: dict[str, Any]) -> str:
    """"The office changes Part 1, Item 2 (Family name) from NOT APPLICABLE to a name on 4 cases: a rule may be missing"."""
    box = f"{g['ref']} ({g['label']})" if g["ref"] and g["label"] else g["ref"] or g["label"]
    return f"The office changes {box} {g['words']} on {len(g['cases'])} cases: a rule may be missing."


def alerts(group_list: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The groups at the threshold, as {id, text}: the line for the morning report and for Reports."""
    return [{"id": g["id"], "text": g["line"]} for g in group_list if len(g["cases"]) >= THRESHOLD]


def form_name(form: str) -> str:
    return accuracy.form_title(form).split(",")[0] if form else "An answer the forms are built from"


def for_reader(data: dict[str, Any] | None, names: dict[str, str], hidden: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The groups for Reports, built from the cases the reader may open only (names: case id -> name for exactly those; hidden: the ones they may not).
    A protected case they may not open is in no row, no count and no line (src/restricted.py's rule): nothing here lets them infer it exists. Never a value."""
    mine = [r for r in (data or {}).get("rows") or [] if r["case"] in names and r["case"] not in hidden]
    out = []
    for g in groups(mine):
        out.append({"id": g["id"], "form": form_name(g["form"]), "box": f"{g['ref']}: {g['label']}" if g["ref"] and g["label"] else g["ref"] or g["label"],
                    "change": g["words"], "cases": len(g["cases"]), "seen": SEEN_BOTH if len(g["seen"]) > 1 else SEEN[g["seen"][0]],
                    "clients": sorted((names[c] for c in g["cases"]), key=str.casefold), "line": g["line"] if len(g["cases"]) >= THRESHOLD else ""})
    return out


def for_case(data: dict[str, Any] | None, case: str, counted=None) -> list[dict[str, Any]]:
    """The case's own changed boxes, with the two values (the case's own page only): each with how many other cases changed the same box the same way.
    counted(case id): whether another case counts among the others (the review app leaves protected cases out: no case's page tells of another one)."""
    rows = [r for r in (data or {}).get("rows") or [] if r["case"] == case or counted is None or counted(r["case"])]
    size = {g["id"]: len(g["cases"]) for g in groups(rows)}
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        if r["case"] != case:
            continue
        key = (r["form"], r["ident"], r["kind"])
        if key in out:  # the same change seen on a form and in a Save: one line, with the person's reason
            out[key]["seen"] = SEEN_BOTH
            if r["source"] == SAVE:
                out[key].update(reason=r.get("reason") or "", by=r.get("by") or "", at=r.get("at") or "")
            continue
        out[key] = {"box": f"{r['ref']}: {r['label']}" if r["ref"] and r["label"] else r["ref"] or r["label"], "form": form_name(r["form"]),
                    "change": change_words(r["kind"], r["noun"]), "before": r["before"], "after": r["after"], "ident": r["ident"], "kind": r["kind"],
                    "seen": SEEN[r["source"]], "reason": r.get("reason") or "", "by": r.get("by") or "", "at": r.get("at") or "",
                    "others": size[group_id(r["form"], r["ident"], r["kind"])] - 1}
    return sorted(out.values(), key=lambda r: (r["form"], r["box"], r["change"]))


# ---------------------------------------------------------------------------------------------- the catalog on disk

def read(clients_root: Path) -> dict[str, Any] | None:
    """The catalog, or None when no audit has run (and nothing was mined yet)."""
    try:
        data = json.loads(path(clients_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("rows"), list) else None


def write(clients_root: Path, data: dict[str, Any]) -> Path:
    """Atomically, owner-only: the catalog holds the values of the boxes the office changed."""
    return _write_private(path(clients_root), data)


def partial_path(clients_root: Path) -> Path:
    """The audit's checkpoint (audit_fill.partial.json beside the catalog): the cases done so far, so a run that stopped can go on (tools/fill_audit.py --resume)."""
    return path(clients_root).with_name("audit_fill.partial.json")


def _write_private(target: Path, data: dict[str, Any]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def _empty() -> dict[str, Any]:
    return {"version": VERSION, "software": "", "forms": None, "saves": None, "rows": [], "skipped": []}


def counts(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """The audit's figures for the accuracy history (tools/accuracy_report.py): what was compared and what changed, as counts. None before any run."""
    if not data or not (data.get("forms") or data.get("saves")):
        return None
    f, s = data.get("forms") or {}, data.get("saves") or {}
    return {"forms_day": f.get("day"), "cases": f.get("cases", 0), "forms": f.get("forms", 0), "boxes": f.get("boxes", 0), "changed": f.get("changed", 0),
            "saves": s.get("saves", 0), "save_cases": s.get("cases", 0), "groups": len(alerts(groups(data["rows"])))}


# ---------------------------------------------------------------------------------------------- the runs

# review items whose answer is a choice, a recorded fact or the product's own approved text, never a value the product got wrong: the Part 14 explanations
# Implementation note.
KEPT_KINDS = ("names", "absence", "declaration", "part14")


def is_save(d: Any) -> bool:
    """A decision in force that changes a value on a review card: set or blank, on an item that is not a name choice, an absence mark or a declaration."""
    return (isinstance(d, dict) and not d.get("undone") and d.get("action") in CHANGES and ((d.get("item") or {}).get("kind") not in KEPT_KINDS))


def _has_saves(client_dir: Path) -> bool:
    try:
        log = json.loads((client_dir / "decisions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(is_save(d) for d in log.values())


def _fill(graph, out: Path) -> None:
    """The I-485 filled from a graph as the product fills it (boxes too long for their field are left out, as src/batch.py finalize_client does)."""
    from fill import field_max_lengths, fill_pdf, load_field_map, map_facts_to_fields

    template = schema_path.path("template", "i485")
    mapping = map_facts_to_fields(graph, load_field_map(schema_path.path("field_map", "i485")))
    limits = field_max_lengths(template)
    for name, value in list(mapping.values.items()):
        if name in limits and isinstance(value, str) and len(value) > limits[name]:
            del mapping.values[name]
    fill_pdf(template, mapping.values, out)


def corrected_pair(case_dir: Path, folder: Path, box_fact: dict[str, str]):
    """The pair to compare for a case with a Save and no hand-filled I-485 of its own. The "reference" is the I-485 with every decision applied, the product's is
    the same case with the Saves taken out (the set and blank decisions): every other decision stays on both sides (an absence mark, the G-28's confirmation, a
    name choice), so the difference is only what a person changed on a review card. Both are filled now, into folder."""
    import absence
    from review.state import load_decisions, reviewed_graph

    kept = {iid: d for iid, d in absence.without_arrived(case_dir, load_decisions(case_dir)).items() if not is_save(d)}
    office, product = reviewed_graph(case_dir), reviewed_graph(case_dir, decisions=kept)
    folder.mkdir(parents=True, exist_ok=True)
    _fill(office, folder / "office.pdf")
    _fill(product, folder / "product.pdf")
    return accuracy.Reference(case_dir.name, "i485", folder / "office.pdf", folder / "product.pdf", office, box_fact, "corrected", accuracy.form_title("i485"))


def _field_map() -> dict[str, Any]:
    from fill import load_field_map

    return load_field_map(schema_path.path("field_map", "i485"))


CHECKPOINT = 25  # corrected cases between writes of the checkpoint


def audit_forms(clients_root: Path, workdir: Path, ref_dir: Path | None = None, log=None, resume: bool = False) -> dict[str, Any]:
    """The monthly audit of the forms. A case with a hand-filled reference is compared to the reference; a case with a Save and no reference is compared corrected
    against uncorrected (corrected_pair); a case with neither is not audited. Cases are done one at a time (nothing but the rows is kept, so memory stays small) and
    a checkpoint is written every CHECKPOINT cases: resume=True goes on from it (the same release only). Returns {"rows", "figures", "skipped"}."""
    import shutil

    import version

    boxes = Boxes()
    rdir = ref_dir or accuracy.reference_dir(clients_root)
    refs, aside = accuracy.firm_references(clients_root, Path(workdir) / "refs", rdir)
    results, more = accuracy.compare_all(refs)
    aside += more
    rows: list[dict[str, Any]] = []
    for r in results:
        rows += rows_from_result(r, boxes, accuracy.read_marks(rdir, r["case"]), "reference")
        reader_examples(clients_root, refs, r, accuracy.read_marks(rdir, r["case"]))
    # what the corrected cases have added so far: kept in the checkpoint, so a run that went on from it counts the whole
    mine: dict[str, Any] = {"done": [], "cases": [], "rows": [], "forms": 0, "boxes": 0, "identical": 0, "skipped": []}
    part = partial_path(clients_root)
    if resume:
        try:
            held = json.loads(part.read_text(encoding="utf-8"))
            if held.get("software") == version.VERSION:
                mine = {k: held[k] for k in mine}
                if log:
                    log(f"  going on from the checkpoint: {len(mine['done'])} corrected cases are done")
        except (OSError, ValueError, KeyError):
            pass
    box_fact = accuracy.facts_by_box(_field_map(), schema_path.path("template", "i485"))
    asked = accuracy.asked_keys()
    skip = {r.case for r in refs if r.form == "i485"} | set(mine["done"])
    todo = sorted(p for p in Path(clients_root).iterdir() if (p / "fact_graph.json").exists() and p.name not in skip and _has_saves(p))
    for n, case_dir in enumerate(todo, 1):
        folder = Path(workdir) / "corrected" / case_dir.name
        try:
            result = accuracy.run_one(corrected_pair(case_dir, folder, box_fact), asked)
            mine["rows"] += rows_from_result(result, boxes, [], "review")
            mine["cases"].append(case_dir.name)
            mine["forms"] += 1
            mine["boxes"] += result["boxes_compared"]
            mine["identical"] += result["identical"]
        except accuracy.Skipped as exc:
            mine["skipped"].append(str(exc))
        except Exception:  # noqa: BLE001 -- one case that cannot be filled must not stop the audit
            mine["skipped"].append(f"{case_dir.name} (Form I-485): the case could not be filled to compare.")
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        mine["done"].append(case_dir.name)
        if n % CHECKPOINT == 0:
            _write_private(part, {"software": version.VERSION, "at": clock.stamp(), **mine})
        if log and n % 100 == 0:
            log(f"  {n} of {len(todo)} corrected cases compared")
    rows += mine["rows"]
    aside += mine["skipped"]
    figures = {"cases": len({r["case"] for r in results} | set(mine["cases"])), "forms": len(results) + mine["forms"], "boxes": sum(r["boxes_compared"] for r in results) + mine["boxes"],
               "identical": sum(r["identical"] for r in results) + mine["identical"], "changed": len(rows)}
    return {"rows": rows, "figures": figures, "skipped": aside}


def reader_examples(clients_root: Path, refs: list, result: dict[str, Any], marks: list[dict[str, Any]]) -> int:
    """The firm's labelled examples (src/reader_examples.py, brief M1): each box of a hand-filled reference that differs from a box a reader filled.
    The Saves on review cards made theirs when they were saved. Never stops the audit."""
    try:
        import reader_examples as examples

        ref = next(f for f in refs if f.case == result["case"] and f.form == result["form"])
        return len(examples.from_reference(clients_root, ref, result, marks))
    except Exception:  # noqa: BLE001 -- an example missing is not worth a failed audit
        return 0


def mine_saves(clients_root: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Every case's Saves (the decision log against what the product held): the rows, and {"cases", "saves"}."""
    boxes = Boxes()
    rows: list[dict[str, Any]] = []
    for case_dir in sorted(p for p in Path(clients_root).iterdir() if (p / "decisions.json").exists() and (p / "fact_graph.json").exists()):
        if (case_dir / "prospect.json").exists():
            continue  # a first call is no case
        try:
            rows += save_rows(case_dir, boxes)
        except Exception:  # noqa: BLE001 -- a case whose log cannot be read adds nothing
            continue
    return rows, {"cases": len({r["case"] for r in rows}), "saves": len(rows)}


def _stamped(extra: dict[str, Any]) -> dict[str, Any]:
    stamp = clock.stamp()
    return {"at": stamp, "day": clock.day(stamp), **extra}


def run(clients_root: Path, workdir: Path, ref_dir: Path | None = None, log=None, who: str | None = None, saves: bool = True, resume: bool = False) -> dict[str, Any]:
    """The whole audit (tools/fill_audit.py): the forms, then the Saves, written to the catalog (and the checkpoint dropped). Returns the catalog. who: the person
    who ran it. resume: go on from the checkpoint a run that stopped left."""
    import version

    held = read(clients_root) or _empty()
    forms = audit_forms(clients_root, workdir, ref_dir, log, resume)
    rows = forms["rows"]
    data = held | {"version": VERSION, "software": version.VERSION, "forms": _stamped(forms["figures"]), "skipped": forms["skipped"]}
    if saves:
        mined, figures = mine_saves(clients_root)
        rows += mined
        data["saves"] = _stamped(figures)
    else:
        rows += [r for r in held["rows"] if r["source"] == SAVE]
    data["rows"] = rows
    write(clients_root, data)
    partial_path(clients_root).unlink(missing_ok=True)
    f = data["forms"]
    events.record("upkeep", "audited", f"Audited the forms: {f['boxes']:,} boxes compared on {f['forms']} forms, {f['changed']} changed by the office", home=accuracy.data_root(clients_root),
                  who=who or WHO, via="tool", default_who=(WHO, "system", "tool"))
    return data


def nightly(clients_root: Path) -> str:
    """The overnight run's step: the reviewers' Saves mined again (the forms' half stays as the last audit left it), the catalog written, and the
    lines for the morning report. Returns the text for the run's log: one line, then one for each box changed the same way on THRESHOLD or more
    cases (the first TOP_LINES)."""
    import version

    data = read(clients_root) or _empty()
    mined, figures = mine_saves(clients_root)
    data = data | {"version": VERSION, "software": version.VERSION, "saves": _stamped(figures), "rows": [r for r in data["rows"] if r["source"] == FORM] + mined}
    write(clients_root, data)
    events.record("upkeep", "mined", f"Counted what the reviewers' Saves change: {figures['saves']} changes on {figures['cases']} cases", home=accuracy.data_root(clients_root))
    # the morning report is one document for all staff: it counts the cases that are not protected, and says so (src/restricted.py)
    shown = _unprotected(clients_root, data["rows"])
    saves = [r for r in shown if r["source"] == SAVE]
    cases = len({r["case"] for r in saves})
    flagged = alerts(groups(shown))
    head = (f"Boxes the office changes: {len(saves)} Save{'s' if len(saves) != 1 else ''} on {cases} case{'s' if cases != 1 else ''}; "
            + (f"{len(flagged)} box{'es' if len(flagged) != 1 else ''} changed the same way on {THRESHOLD} or more cases (Reports, Boxes the office changes)."
               if flagged else f"no box is changed the same way on {THRESHOLD} or more cases."))
    return "\n".join([head] + [a["text"] for a in flagged[:TOP_LINES]] + ([f"{len(flagged) - TOP_LINES} more are on Reports."] if len(flagged) > TOP_LINES else [])
                     + [PROTECTED_NOTE])


PROTECTED_NOTE = "Protected cases are not counted here: they are audited for the people who may open them, on each case's own page."


def _unprotected(clients_root: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows of the cases that are not protected (src/restricted.py is_restricted); a case that cannot be asked is left out, as it is everywhere."""
    import restricted

    ok: dict[str, bool] = {}
    for r in rows:
        if r["case"] not in ok:
            try:
                ok[r["case"]] = not restricted.is_restricted(Path(clients_root) / r["case"])
            except Exception:  # noqa: BLE001 -- fail closed
                ok[r["case"]] = False
    return [r for r in rows if ok[r["case"]]]
