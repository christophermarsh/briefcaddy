"""tools/export_firm.py --pack: the security answer pack, with the provider's name and security contact filled into the product data
statement, and refused while a placeholder is left."""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import deployment  # noqa: E402
import export_firm  # noqa: E402

SHIPPED = (REPO / "docs" / "security" / "product_data_statement.md").read_text(encoding="utf-8")


def _provider(monkeypatch, tmp_path, **provider):
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps({"mode": "hosted", "provider": provider}), encoding="utf-8")
    monkeypatch.setattr(deployment, "PATH", path)


def test_the_shipped_statement_keeps_its_placeholder_and_the_tool_knows_where_it_goes():
    assert export_firm.STATEMENT_LINE.search(SHIPPED) and "[decided by the provider: the provider's legal name" in SHIPPED
    filled = export_firm.fill_statement(SHIPPED, {"name": "Acme Legal Software", "email": "security@acme.example", "phone": "555-0100"})
    assert "Provider: Acme Legal Software. Security contact: security@acme.example, 555-0100.\n" in filled
    assert not export_firm.PLACEHOLDER.search(filled) and "The provider fills this line in" not in filled
    assert filled.replace("Provider: Acme Legal Software. Security contact: security@acme.example, 555-0100.\n", "") != filled
    only_email = export_firm.fill_statement(SHIPPED, {"name": "Acme", "email": "s@acme.example", "phone": ""})
    assert "Security contact: s@acme.example.\n" in only_email


@pytest.mark.parametrize("provider,words", [({}, ["provider's name", "security contact's email"]), ({"name": "the software provider", "email": "s@x.example"}, ["provider's name"]),
                                            ({"name": "Acme", "email": ""}, ["security contact's email"]), ({"name": "  ", "email": "s@x.example"}, ["provider's name"])])
def test_the_pack_is_refused_with_what_is_missing_and_nothing_is_written(monkeypatch, tmp_path, capsys, provider, words):
    _provider(monkeypatch, tmp_path, **provider)
    out = tmp_path / "out"
    assert export_firm.main(["--pack", "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("Not exported: the statement needs ") and all(w in err for w in words) and "deployment.json" in err
    assert not out.exists()


def test_the_pack_holds_every_document_with_the_statement_filled_and_the_others_as_shipped(monkeypatch, tmp_path, capsys):
    _provider(monkeypatch, tmp_path, name="Acme Legal Software", email="security@acme.example", phone="555-0100")
    assert export_firm.main(["--pack", "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    zips = list((tmp_path / "out").glob("i485-security-pack-*.zip"))
    assert len(zips) == 1 and f"Wrote {zips[0]}" in out
    shipped = {p.name for p in (REPO / "docs" / "security").glob("*.md")}
    with zipfile.ZipFile(zips[0]) as zf:
        assert {n.split("/", 1)[1] for n in zf.namelist()} == shipped and all(n.startswith("security-pack/") for n in zf.namelist())
        statement = zf.read("security-pack/product_data_statement.md").decode()
        assert "Provider: Acme Legal Software. Security contact: security@acme.example, 555-0100." in statement
        assert "[decided by the provider" not in statement
        for name in shipped - {"product_data_statement.md"}:
            assert zf.read(f"security-pack/{name}").decode() == (REPO / "docs" / "security" / name).read_text(encoding="utf-8")
    assert (REPO / "docs" / "security" / "product_data_statement.md").read_text(encoding="utf-8") == SHIPPED  # the repository's copy keeps its brackets
    assert "decision(s) still marked [decided by the provider]" in out  # the other documents' open decisions are the provider's, said at the end
    # never over an earlier file
    target = tmp_path / "pack.zip"
    assert export_firm.main(["--pack", "--out", str(target)]) == 0 and export_firm.main(["--pack", "--out", str(target)]) == 2
    assert "already exists" in capsys.readouterr().err


def test_a_placeholder_left_in_the_statement_is_listed_and_refused(monkeypatch, tmp_path, capsys):
    _provider(monkeypatch, tmp_path, name="Acme", email="s@acme.example")
    fake = tmp_path / "repo"
    shutil.copytree(REPO / "docs" / "security", fake / "docs" / "security")
    path = fake / "docs" / "security" / "product_data_statement.md"
    path.write_text(SHIPPED + "\nOur data center: [decided by the provider: where the hosted service runs]\n", encoding="utf-8")
    monkeypatch.setattr(export_firm, "REPO", fake)
    assert export_firm.main(["--pack", "--out", str(tmp_path / "out")]) == 2
    err = capsys.readouterr().err
    assert "placeholders are still in the statement" in err and "where the hosted service runs" in err and not (tmp_path / "out").exists()
    path.write_text(SHIPPED.replace("The provider fills this line in", "Fill this in"), encoding="utf-8")  # the shipped line changed: no silent export
    assert export_firm.main(["--pack", "--out", str(tmp_path / "out")]) == 2 and "no place for the provider's name" in capsys.readouterr().err
