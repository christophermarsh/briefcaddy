"""Where the schemas live (src/schema_path.py): every kind resolves, no code types a schema path, no file is orphaned, and the names a schema
writes for another schema (a form's template, a cover letter's base, a packet's letter) reach the file."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

import schema_path
from schema_path import LAYOUT

REPO = Path(__file__).resolve().parents[1]
SCHEMAS = schema_path.ROOT
CODE = [*sorted((REPO / "src").rglob("*.py")), *sorted((REPO / "tools").rglob("*.py")), *sorted((REPO / "tests").rglob("*.py")),
        *sorted((REPO / "tools").glob("*.sh")), *sorted(REPO.glob("*.sh")), *sorted(REPO.glob("*.ps1")), *sorted((REPO / ".github").rglob("*.yml"))]
ME = {REPO / "src" / "schema_path.py", Path(__file__).resolve()}


def test_every_kind_resolves_to_files_that_exist():
    for kind in LAYOUT:
        names = schema_path.names(kind)
        assert names, f"no file of kind {kind}"
        for name in names:
            assert schema_path.path(kind, name).is_file(), (kind, name)
        assert [schema_path.path(kind, n) for n in names] == schema_path.glob(kind)


def test_an_unknown_kind_is_one_clear_error():
    with pytest.raises(KeyError, match="no schema kind"):
        schema_path.path("nonsense", "x")


def test_every_file_in_schemas_is_reached_through_exactly_one_kind():
    reached: dict[Path, str] = {}
    for kind in LAYOUT:
        for p in schema_path.glob(kind):
            assert p not in reached, f"{p} is of kind {kind} and of kind {reached[p]}"
            reached[p] = kind
    everything = set(schema_path.all_files())
    assert everything == set(reached), sorted(p.relative_to(SCHEMAS).as_posix() for p in everything ^ set(reached))
    assert not [p for p in SCHEMAS.iterdir() if p.is_file()], "a schema file sits loose in schemas/: it belongs in the folder of its kind"


def test_every_folder_of_schemas_is_named_in_the_architecture_notes():
    notes = (REPO / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    for folder in sorted(p.name for p in SCHEMAS.iterdir() if p.is_dir()):
        assert f"schemas/{folder}/" in notes, f"docs/ARCHITECTURE.md says nothing of schemas/{folder}/"


def test_the_names_one_schema_writes_for_another_reach_the_file():
    for kind in ("cover_letter", "packet"):
        for p in schema_path.glob(kind):
            data = json.loads(p.read_text(encoding="utf-8"))
            for key in ("base", "lockbox_chart") if kind == "cover_letter" else ("cover_letter",):
                if isinstance(data.get(key), str):
                    assert schema_path.named(data[key]).is_file(), (p.name, key, data[key])
    companion = json.loads(schema_path.path("packet", "companion_forms").read_text(encoding="utf-8"))
    for fid, form in companion["forms"].items():
        if form.get("template"):
            assert schema_path.named(form["template"]).is_file(), (fid, form["template"])
    assert schema_path.named("cover_letter.json") == schema_path.path("cover_letter", "i485")
    assert schema_path.named("uscis_lockboxes_nfb.json") == schema_path.path("law", "uscis_lockboxes_nfb")
    with pytest.raises(KeyError):
        schema_path.named("fees.json")


def test_packet_py_names_the_same_filings_as_the_packets_folder():
    import packet

    filings = set(schema_path.names("packet")) - {"companion_forms"}
    assert set(packet.FILINGS) == filings
    for filing, manifest in packet.FILINGS.items():  # the file a built packet leaves in the client's folder is the schema's own "manifest"
        assert packet.load_filing(filing).get("manifest", "packet.json") == manifest, filing


def test_a_root_other_than_the_shipped_one_is_honoured(tmp_path):
    assert schema_path.path("law", "fees", tmp_path) == tmp_path / "law" / "fees.json"
    assert schema_path.glob("law", tmp_path) == [] and schema_path.names("question", tmp_path) == []
    assert schema_path.schemas_in(tmp_path) == tmp_path / "schemas"


def test_rel_reads_as_the_sentence_and_the_register_say_it():
    assert schema_path.rel("law", "fees") == "schemas/law/fees.json" and schema_path.rel("template", "i485") == "schemas/forms/i485/template.pdf"
    assert (REPO / schema_path.rel("field_map", "i485")).is_file()


FLAT = re.compile(r"schemas/[A-Za-z0-9_]+\.(?:json|pdf)\b")


@pytest.mark.parametrize("file", [p for p in CODE if p not in ME], ids=lambda p: p.relative_to(REPO).as_posix())
def test_no_code_types_a_schema_path(file):
    text = file.read_text(encoding="utf-8")
    flat = FLAT.findall(text)
    assert not flat, f'{file.name} types a schema path ({flat[0]}): reach it through src/schema_path.py (docs/ARCHITECTURE.md, "The schemas folder")'
    if file.suffix == ".py":
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) and node.right.value == "schemas":
                raise AssertionError(f'{file.name}:{node.lineno} builds a path to schemas/ by hand: use schema_path.path / folder / schemas_in')
