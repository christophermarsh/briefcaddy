"""tools/new_edition.py: a new blank edition of a form, compared with the template and the field map we have.

The "new edition" here is the real template itself (nothing changed: the tool must say so), and copies of it with one box renamed,
moved, removed, given a new tooltip or a new on-value. The tool never edits the map or any file of the product."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject, TextStringObject
import schema_path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import new_edition  # noqa: E402

TEMPLATE = schema_path.path("template", "i765")


def _short(full: str) -> str:
    return re.split(r"(?<!\\)\.", full)[-1]


def _single_use_box(form: str, want_on: bool = False) -> tuple[str, str, str | None]:
    """(a mapped box that is one widget in the template, the fact key it fills, its on-value if it has one)."""
    template, _file, fmap = new_edition.load_map(form)
    fields = new_edition.fields_of(template)
    index = new_edition._index(fields)
    for fact, spec in fmap.items():
        for name, on in new_edition._uses(spec):
            full = index.get(name, [])
            if len(full) == 1 and len(fields[full[0]]) == 1 and bool(on) == want_on and name == _short(full[0]):
                return name, fact, on
    raise AssertionError("no such box")


def _edit(tmp_path: Path, name: str, short: str, change) -> Path:
    """A copy of the I-765 template where the box with this short name has been changed by change(widget)."""
    writer = PdfWriter(clone_from=str(TEMPLATE))
    hit = 0
    for page in writer.pages:
        for ref in list(page.get("/Annots") or []):
            a = ref.get_object()
            if a.get("/Subtype") == "/Widget" and new_edition._name(a) and _short(new_edition._name(a)) == short:
                change(a, page, ref)
                hit += 1
    assert hit == 1
    out = tmp_path / name
    with open(out, "wb") as f:
        writer.write(f)
    return out


def test_the_template_against_itself_has_nothing_to_report():
    for form in ("i765", "i485", "n400"):
        template, _file, _fmap = new_edition.load_map(form)
        r = new_edition.diff(form, template)
        assert not new_edition.needs_a_person(r) and r["new"] == [] and r["unchanged"] > 20, form
        assert r["old_edition"] == r["new_edition"]


def test_a_renamed_box_is_reported_as_moved_with_its_new_name(tmp_path):
    short, fact, _ = _single_use_box("i765")
    new = _edit(tmp_path, "renamed.pdf", short, lambda a, page, ref: a.__setitem__(NameObject("/T"), TextStringObject("Pt2Line99_Renamed[0]")))
    r = new_edition.diff("i765", new)
    [moved] = r["moved"]
    assert moved["name"] == short and fact in moved["facts"] and moved["renamed_to"] == ["Pt2Line99_Renamed[0]"]
    assert r["missing"] == [] and r["tooltips"] == [] and r["on_values"] == [] and r["new"] == []  # the renamed box is not also "new"
    assert new_edition.needs_a_person(r)


def test_a_box_that_went_somewhere_else_is_moved_and_a_box_that_vanished_is_missing(tmp_path):
    short, fact, _ = _single_use_box("i765")

    def shift(a, page, ref):
        r = [float(v) for v in a["/Rect"]]
        a[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in (r[0] + 80, r[1], r[2] + 80, r[3])])

    [moved] = new_edition.diff("i765", _edit(tmp_path, "moved.pdf", short, shift))["moved"]
    assert moved["name"] == short and "renamed_to" not in moved and moved["was"] != moved["now"]

    def remove(a, page, ref):
        page["/Annots"].remove(ref)

    r = new_edition.diff("i765", _edit(tmp_path, "gone.pdf", short, remove))
    assert [m["name"] for m in r["missing"]] == [short] and fact in r["missing"][0]["facts"] and r["moved"] == []


def test_a_new_tooltip_and_a_new_on_value_are_reported(tmp_path):
    short, _fact, _ = _single_use_box("i765")
    r = new_edition.diff("i765", _edit(tmp_path, "tip.pdf", short, lambda a, page, ref: a.__setitem__(NameObject("/TU"), TextStringObject("Something else entirely"))))
    [tip] = r["tooltips"]
    assert tip["name"] == short and tip["now"] == "Something else entirely" and tip["was"] != tip["now"]
    box, _fact2, on = _single_use_box("i765", want_on=True)

    def reopen(a, page, ref):
        states = a["/AP"]["/N"]
        states[NameObject("/ZZ")] = states.pop(on)

    r = new_edition.diff("i765", _edit(tmp_path, "on.pdf", box, reopen))
    [bad] = r["on_values"]
    assert bad["name"] == box and bad["expects"] == on and "/ZZ" in bad["now_offers"]


def test_the_command_writes_a_report_and_pictures_and_never_touches_the_product(tmp_path, capsys):
    short, fact, _ = _single_use_box("i765")
    new = _edit(tmp_path, "renamed.pdf", short, lambda a, page, ref: a.__setitem__(NameObject("/T"), TextStringObject("Pt2Line99_Renamed[0]")))
    watched = [schema_path.path("packet", "companion_forms"), TEMPLATE, schema_path.path("register", "maintenance"), schema_path.path("field_map", "i485")]
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in watched]
    out = tmp_path / "pictures"
    assert new_edition.main(["--form", "i765", "--pdf", str(new), "--out", str(out)]) == 1  # something needs a person
    text = capsys.readouterr().out
    assert "MOVED or RENAMED (1)" in text and short in text and "Pt2Line99_Renamed[0]" in text and fact in text
    assert "companion_forms.json" in text and "What to do next:" in text
    assert (out / "report.txt").read_text(encoding="utf-8").startswith("i765: the edition we fill is 08/21/25")
    assert len(list(out.glob("page_*.png"))) == len(PdfReader(str(TEMPLATE)).pages)
    assert "Pages not marked DIFFERENT still need a look" in text
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in watched]  # it never edits the map, a template or the register
    # nothing changed: exit 0 and a short answer
    assert new_edition.main(["--form", "i765", "--pdf", str(TEMPLATE), "--out", str(tmp_path / "same"), "--no-pictures"]) == 0
    assert "nothing to update" in capsys.readouterr().out


def test_the_n400_is_mapped_by_position_and_the_tool_says_to_remap_it_that_way(tmp_path):
    template, _file, _fmap = new_edition.load_map("n400")
    r = new_edition.diff("n400", template)
    steps = new_edition.next_steps(r | {"new_edition": "12/31/26"}, None, [])
    assert any("re-map it by position (the generator is not in the repo" in s and "N-400" in s and "scratchpad" not in s for s in steps)
    assert not any("by position" in s for s in new_edition.next_steps(new_edition.diff("i765", TEMPLATE), None, []))


def test_the_pictures_put_both_editions_side_by_side_and_mark_the_pages_that_differ(tmp_path):
    same = new_edition.render_side_by_side(TEMPLATE, TEMPLATE, tmp_path / "same")
    assert len(same) == 7 and not any(p["different"] for p in same) and all((tmp_path / "same" / p["file"]).exists() for p in same)
    other = new_edition.render_side_by_side(TEMPLATE, schema_path.path("template", "i864"), tmp_path / "other")
    assert any(p["different"] and p["file"].endswith("_DIFFERENT.png") for p in other)
    assert len(other) == max(len(PdfReader(str(TEMPLATE)).pages), len(PdfReader(str(schema_path.path("template", "i864"))).pages))


def test_an_unknown_form_names_the_forms_it_knows():
    with pytest.raises(SystemExit, match="No form 'i999'.*i485.*i765"):
        new_edition.load_map("i999")
