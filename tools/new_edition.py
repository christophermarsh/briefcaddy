"""A new USCIS edition of a form we fill: what changed, against the field map we have. For us (the provider), never for a firm.

    python tools/new_edition.py --form i485 --pdf ~/Downloads/i-485.pdf [--out folder]

--form is the form's id in schemas/packets/companion_forms.json (i765, n400, i130, g28...) or i485 (its own map, schemas/forms/i485/field_map.json).
--pdf is the new blank PDF downloaded from the form's uscis.gov page (never from anywhere else).

It reads the new PDF's fields with pypdf (names, tooltips, page, place) and compares them with the template we fill now and with
every box the map uses:

  moved      a mapped box that is now somewhere else on the page or on another page, or that has a new name in the same place
             (the new name is shown: the map entry needs it)
  missing    a mapped box that is gone: the run would stop with its name
  tooltips   a mapped box whose tooltip (the form's own description of it) changed: read it, the box may mean something else now
  on-values  a check box whose "on" value changed: filling it would tick nothing
  new        boxes the form did not have before (nothing in the map uses them)

It also renders both editions' pages to PNG side by side in a folder (old on the left, new on the right; the file names say which
pages look different) for the visual check, and prints what to do next. It never edits the map, a template or any other file
of the product: the only files it writes are those pictures and report.txt, in the folder.

Exit code 0: nothing a map entry depends on changed; 1: something needs a person.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import schema_path  # noqa: E402

import logging  # noqa: E402

from pypdf import PdfReader  # noqa: E402

logging.getLogger("pypdf").setLevel(logging.ERROR)  # the N-400 makes pypdf complain about its own padding, every time

SCHEMAS = schema_path.ROOT
POSITION_MAPPED = {"n400"}  # the forms whose map was built from each box's position on the page, because its names and tooltips are often wrong
TOLERANCE = 10.0  # points: a box that moved less than this is where it was
DIFFERENT = 0.001  # the share of a page's pixels that must differ for the page to count as different


# -- reading a PDF -------------------------------------------------------------------------------------------


def _name(obj: Any) -> str:
    """A widget's full field name: its own /T and every parent's, joined with dots."""
    parts = []
    while obj is not None:
        t = obj.get("/T")
        if t is not None:
            parts.append(str(t))
        obj = obj["/Parent"].get_object() if "/Parent" in obj else None
    return ".".join(reversed(parts))


def _inherited(obj: Any, key: str) -> Any:
    while obj is not None:
        if key in obj:
            return obj[key]
        obj = obj["/Parent"].get_object() if "/Parent" in obj else None
    return None


def fields_of(path: Path) -> dict[str, list[dict[str, Any]]]:
    """{full field name: [each widget: page (1-based), x, y (its centre), tooltip, kind, states (a check box's "on" values)]}."""
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    out: dict[str, list[dict[str, Any]]] = {}
    for n, page in enumerate(reader.pages, start=1):
        for ref in page.get("/Annots") or []:
            a = ref.get_object()
            if a.get("/Subtype") != "/Widget":
                continue
            full = _name(a)
            if not full:
                continue
            rect = [float(v) for v in a.get("/Rect", [0, 0, 0, 0])]
            tu = _inherited(a, "/TU")
            ap = (a.get("/AP") or {}).get("/N")
            states = sorted(str(k) for k in (ap.get_object().keys() if ap is not None and hasattr(ap.get_object(), "keys") else []) if str(k) != "/Off")
            out.setdefault(full, []).append({"page": n, "x": (rect[0] + rect[2]) / 2, "y": (rect[1] + rect[3]) / 2,
                                             "tooltip": re.sub(r"\s+", " ", str(tu)).strip() if tu is not None else "",
                                             "kind": str(_inherited(a, "/FT") or ""), "states": states})
    return out


def _index(fields: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    """Every name a map can use for a box: its full name, its short name, and its short name with the page group before it
    (src/fill/companion.py field_map_for: the I-131 reuses a short name on four pages)."""
    names: dict[str, list[str]] = {}
    for full in fields:
        parts = re.split(r"(?<!\\)\.", full)  # the N-400 names a box "P9_Line7\.b\.[0]": a dot in the name is escaped
        for key in {full, parts[-1], ".".join(parts[-2:])}:
            names.setdefault(key, []).append(full)
    return names


# -- what the map uses -----------------------------------------------------------------------------------------


def _uses(spec: Any) -> list[tuple[str, str | None]]:
    """[(box name, its on-value or None)] for one map entry (src/fill/field_map.py's shapes)."""
    if isinstance(spec, list):  # the I-485 map's shorthand: a plain list of boxes
        return [(s, None) for s in spec if isinstance(s, str)]
    out: list[tuple[str, str | None]] = []
    out += [(s, None) for s in spec.get("fields", [])]
    out += [(spec[k], None) for k in ("feet_field", "inches_field") if isinstance(spec.get(k), str)]
    for side in ("yes", "no"):
        if side in spec:
            out.append((spec[side][0], spec[side][1]))
    for option in (spec.get("options") or {}).values():
        for name, on in (option if isinstance(option[0], list) else [option]):
            out.append((name, on))
    return out


def load_map(form: str, schemas: Path = SCHEMAS) -> tuple[Path, Path, dict[str, Any]]:
    """(the template, the file that holds the map, {fact key: map entry}) for a form id."""
    if form == "i485":
        data = json.loads((schema_path.path("field_map", "i485", schemas)).read_text(encoding="utf-8"))
        return schema_path.path("template", "i485", schemas), schema_path.path("field_map", "i485", schemas), data["fact_to_acroform"]
    from fill.companion import load_profile

    profile = load_profile(schema_path.path("packet", "companion_forms", schemas))
    if form not in profile["forms"]:
        raise SystemExit(f"No form {form!r}. The forms: i485, " + ", ".join(sorted(profile["forms"])) + ".")
    entry = profile["forms"][form]
    return schema_path.named(entry["template"], schemas), schema_path.path("packet", "companion_forms", schemas), entry["map"]


# -- the comparison -----------------------------------------------------------------------------------------------


def _place(w: dict[str, Any]) -> str:
    return f"page {w['page']}, {w['x']:.0f},{w['y']:.0f}"


def _close(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a["page"] == b["page"] and abs(a["x"] - b["x"]) <= TOLERANCE and abs(a["y"] - b["y"]) <= TOLERANCE


def _locate(ref: str, old: list[dict[str, Any]], new: dict[str, list[dict[str, Any]]], new_index: dict[str, list[str]]) -> list[str]:
    """The new PDF's box (or boxes) a map name stands for: the same name, else the same short name where the page group changed."""
    if ref in new_index:
        return new_index[ref]
    short = re.split(r"(?<!\\)\.", ref)[-1]
    return new_index.get(short, [])


def diff(form: str, new_pdf: Path, schemas: Path = SCHEMAS) -> dict[str, Any]:
    """The comparison of the new PDF with the template and the map of `form` (the report's data; nothing is written)."""
    template, map_file, fmap = load_map(form, schemas)
    old, new = fields_of(template), fields_of(new_pdf)
    old_index, new_index = _index(old), _index(new)
    facts: dict[str, list[str]] = {}
    ons: dict[str, set[str]] = {}
    for fact, spec in fmap.items():
        for name, on in _uses(spec):
            facts.setdefault(name, []).append(fact)
            if on:
                ons.setdefault(name, set()).add(on)
    report: dict[str, Any] = {"form": form, "template": template.relative_to(schemas).as_posix(), "map": map_file.relative_to(schemas).as_posix(), "old_edition": None, "new_edition": None,
                              "moved": [], "missing": [], "tooltips": [], "on_values": [], "new": [], "unchanged": 0, "not_in_template": []}
    from maintenance import pdf_edition

    report["old_edition"], report["new_edition"] = pdf_edition(template), pdf_edition(new_pdf)
    old_short = {re.split(r"(?<!\\)\.", f)[-1] for f in old}
    taken: set[str] = set()  # the new boxes a map name was matched to
    for ref in sorted(facts):
        old_full = old_index.get(ref)
        if not old_full:
            report["not_in_template"].append(ref)  # the map already names a box this template hasn't: not this tool's business
            continue
        old_widgets = [w for f in old_full for w in old[f]]
        new_full = _locate(ref, old_widgets, new, new_index)
        entry = {"name": ref, "facts": sorted(set(facts[ref]))}
        if not new_full:
            renamed = _renamed_to(old_widgets, new, old_short, taken)
            if renamed:
                taken.update(renamed)
                report["moved"].append(entry | {"was": _place(old_widgets[0]), "renamed_to": sorted({re.split(r"(?<!\\)\.", f)[-1] for f in renamed}),
                                                "now": _place(new[renamed[0]][0])})
            else:
                report["missing"].append(entry | {"was": _place(old_widgets[0])})
            continue
        new_widgets = [w for f in new_full for w in new[f]]
        taken.update(new_full)
        problems = [o for o in old_widgets if not any(_close(o, n) for n in new_widgets)]
        if problems or len(new_widgets) != len(old_widgets):
            first = problems[0] if problems else old_widgets[0]
            near = min(new_widgets, key=lambda n: (n["page"] != first["page"], abs(n["x"] - first["x"]) + abs(n["y"] - first["y"])))
            report["moved"].append(entry | {"was": _place(first), "now": _place(near),
                                            "copies": f"{len(old_widgets)} before, {len(new_widgets)} now" if len(new_widgets) != len(old_widgets) else None})
            continue
        before, after = old_widgets[0]["tooltip"], new_widgets[0]["tooltip"]
        bad = False
        if before != after:
            report["tooltips"].append(entry | {"was": before, "now": after})
            bad = True
        for on in sorted(ons.get(ref, ())):
            offered = sorted({s for w in new_widgets for s in w["states"]})
            if offered and on not in offered:
                report["on_values"].append(entry | {"expects": on, "now_offers": offered})
                bad = True
        report["unchanged"] += 0 if bad else 1
    new_short = {f: re.split(r"(?<!\\)\.", f)[-1] for f in new}
    report["new"] = sorted({s for f, s in new_short.items() if s not in old_short and f not in taken})
    return report


def _renamed_to(old_widgets: list[dict[str, Any]], new: dict[str, list[dict[str, Any]]], old_short: set[str], taken: set[str]) -> list[str]:
    """New boxes (a name the old template didn't have) sitting where the missing box was: it was renamed, not removed."""
    out = []
    for full, widgets in new.items():
        if full in taken or re.split(r"(?<!\\)\.", full)[-1] in old_short:
            continue
        if any(_close(o, n) for o in old_widgets for n in widgets):
            out.append(full)
    return out


def needs_a_person(r: dict[str, Any]) -> bool:
    return bool(r["moved"] or r["missing"] or r["tooltips"] or r["on_values"])


# -- the pictures -----------------------------------------------------------------------------------------------------


def render_side_by_side(old_pdf: Path, new_pdf: Path, out: Path, scale: float = 1.0) -> list[dict[str, Any]]:
    """page_NN.png (old on the left, new on the right) for every page of either edition; returns [{page, file, different}]."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageChops, ImageDraw

    out.mkdir(parents=True, exist_ok=True)
    a, b = pdfium.PdfDocument(str(old_pdf)), pdfium.PdfDocument(str(new_pdf))
    pages = []
    for n in range(max(len(a), len(b))):
        left = a[n].render(scale=scale).to_pil().convert("RGB") if n < len(a) else None
        right = b[n].render(scale=scale).to_pil().convert("RGB") if n < len(b) else None
        w, h = (left or right).size
        sheet = Image.new("RGB", (w * 2 + 20, h + 22), "white")
        draw = ImageDraw.Draw(sheet)
        draw.text((4, 5), f"OLD edition: page {n + 1}" if left else "OLD edition: no such page", fill="black")
        draw.text((w + 24, 5), f"NEW edition: page {n + 1}" if right else "NEW edition: no such page", fill="black")
        if left:
            sheet.paste(left, (0, 22))
        if right:
            sheet.paste(right, (w + 20, 22))
        different = left is None or right is None or left.size != right.size
        if not different:
            gray = ImageChops.difference(left, right).convert("L").point(lambda v: 255 if v > 60 else 0)
            different = gray.histogram()[255] / (w * h) > DIFFERENT
        name = out / f"page_{n + 1:02d}{'_DIFFERENT' if different else ''}.png"
        sheet.save(name)
        pages.append({"page": n + 1, "file": name.name, "different": different})
    return pages


# -- what to do next ------------------------------------------------------------------------------------------------


def next_steps(r: dict[str, Any], pictures: Path | None, different: list[int]) -> list[str]:
    form, template = r["form"], r["template"]
    steps = []
    if r["old_edition"] and r["old_edition"] == r["new_edition"]:
        steps.append(f"The new PDF is edition {r['new_edition']}, the one we already fill: nothing to update. (USCIS may have changed the form page, not the form.)")
        if not needs_a_person(r):
            return steps
    if r["missing"]:
        steps.append(f"Missing boxes: the run would stop on each. Find where the form put each one (open the new PDF, read the tooltips) and fix its entry in {r['map']}.")
    if r["moved"]:
        steps.append(f"Moved or renamed boxes: re-check each map entry in {r['map']} listed above (the fact keys are named), using the new name where one is shown.")
    if r["tooltips"]:
        steps.append("Changed tooltips: read the old and new wording; if the box now asks something else, its entry fills the wrong answer.")
    if r["on_values"]:
        steps.append("Check boxes with a new on-value: put the new value in the entry's option, or the box stays empty.")
    if form in POSITION_MAPPED:
        steps.append(f"The {re.sub(r'^([a-z]+)(\d)', lambda m: m.group(1).upper() + '-' + m.group(2), form)} is mapped by position, not by name (its box names and tooltips are often wrong): re-map it by position "
                     "(the generator is not in the repo; docs/maintenance.md, 'When a USCIS form edition changes', and docs/decisions.md say how the N-400 was mapped), "
                     "then compare every page against the pictures. Do not trust the lists above for it.")
    if r["new"]:
        steps.append(f"{len(r['new'])} new boxes (listed in the report): if the form now asks something the case holds, add them to {r['map']}.")
    if different and pictures:
        steps.append(f"Look at the pictures in {pictures} (the files marked DIFFERENT first: pages {', '.join(map(str, different))}) for questions that moved, were added or reworded.")
    steps += [f"Then, yourself: save the new PDF over schemas/{template}, run the whole test suite, build a sample packet and look at every filled page "
              "(docs/maintenance.md, 'When a USCIS form edition changes'), mark the register item checked, and note the change in docs/decisions.md."]
    return steps


def text_report(r: dict[str, Any], pages: list[dict[str, Any]], pictures: Path | None) -> str:
    L = [f"{r['form']}: the edition we fill is {r['old_edition'] or 'unknown'}; the new PDF is {r['new_edition'] or 'unknown'}.",
         f"Compared with schemas/{r['template']} and the map {r['map']}: {r['unchanged']} mapped boxes unchanged."]

    def section(title: str, rows: list[dict[str, Any]], line) -> None:
        if rows:
            L.append(f"\n{title} ({len(rows)}):")
            L.extend("  " + line(x) for x in rows)

    section("MOVED or RENAMED", r["moved"], lambda x: f"{x['name']}  (facts: {', '.join(x['facts'][:4])}): was {x['was']}, now {x['now']}"
            + (f", new name {', '.join(x['renamed_to'])}" if x.get("renamed_to") else "") + (f" [{x['copies']}]" if x.get("copies") else ""))
    section("MISSING from the new PDF", r["missing"], lambda x: f"{x['name']}  (facts: {', '.join(x['facts'][:4])}): was {x['was']}")
    section("TOOLTIP changed", r["tooltips"], lambda x: f"{x['name']}: \"{x['was']}\" -> \"{x['now']}\"")
    section("ON-VALUE changed", r["on_values"], lambda x: f"{x['name']}: the map expects {x['expects']}, the box now offers {', '.join(x['now_offers'])}")
    if r["new"]:
        L.append(f"\nNEW boxes ({len(r['new'])}): " + ", ".join(r["new"][:40]) + (" ..." if len(r["new"]) > 40 else ""))
    if r["not_in_template"]:
        L.append(f"\n{len(r['not_in_template'])} names in the map are not in the current template either (already broken before this edition): " + ", ".join(r["not_in_template"][:10]))
    if not needs_a_person(r):
        L.append("\nNo mapped box moved, vanished or changed its tooltip or on-value.")
    different = [p["page"] for p in pages if p["different"]]
    if pages:
        L.append(f"\nPictures: {len(pages)} pages. Pages not marked DIFFERENT still need a look: a one-word change is too small for the check to see "
                 "(and a form can change only in its words).")
    L.append("\nWhat to do next:")
    L.extend(f"  {i}. {s}" for i, s in enumerate(next_steps(r, pictures, different), start=1))
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True, help="i485, or a form id in schemas/packets/companion_forms.json")
    ap.add_argument("--pdf", required=True, type=Path, help="the new blank PDF, downloaded from the form's uscis.gov page")
    ap.add_argument("--out", type=Path, help="where the pictures and report.txt go (default: data/new_editions/<form>)")
    ap.add_argument("--no-pictures", action="store_true", help="skip the page pictures")
    args = ap.parse_args(argv)
    if not args.pdf.exists():
        raise SystemExit(f"{args.pdf}: no such file")
    template, _map_file, _fmap = load_map(args.form)
    r = diff(args.form, args.pdf)
    out = args.out or REPO / "data" / "new_editions" / args.form
    pages = [] if args.no_pictures else render_side_by_side(template, args.pdf, out)
    report = text_report(r, pages, None if args.no_pictures else out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 1 if needs_a_person(r) else 0


if __name__ == "__main__":
    sys.exit(main())
