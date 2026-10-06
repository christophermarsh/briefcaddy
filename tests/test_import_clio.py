"""tools/import_clio.py: a Clio export (contacts, matters, documents) into client folders and the portal, for a firm that switches without connecting.

Everything here is made up: the people are "Ana Clara Exemplo Souza" and friends, the column headings are the importer's own guesses from Clio's API
objects (schemas/firm/import_clio_columns.json: Clio's help pages for its export refused the request, so no real export's columns were read), and the "PDFs"
are a few bytes. Nothing touches the network or any real client's folder. The engine is tools/import_docketwise.py's (tests/test_import_docketwise.py
holds the rest of what it does); these tests are about what differs: the system's name and prefix, the practice area as the type, a closed matter, and
the restriction of a protected matter the first time it comes in."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import import_clio as imp  # noqa: E402
import import_docketwise as engine  # noqa: E402
import schema_path

REPO = Path(__file__).resolve().parents[1]
CONTACTS = (
    "Contact ID,First Name,Last Name,Primary Email Address,Primary Phone Number\n"
    "501,Ana Clara,Exemplo Souza,ana.exemplo@example.com,+1 617 555 0100\n"
    "502,Maria,Exemplo,maria.exemplo@example.com,+1 305 555 0101\n"
    "503,Jean,Egzanp,,+1 305 555 0102\n"
)
MATTERS = (
    "Matter ID,Matter Number,Description,Client,Practice Area,Status\n"
    "101,00101-Souza,Souza green card,Ana Clara Exemplo Souza,Immigration,Open\n"
    "102,00102-Exemplo,Maria family petition,Maria Exemplo,Family Immigration,Pending\n"
    "103,00103-Old,Old file,Jean Egzanp,Immigration,Closed\n"
    "104,00104-VAWA,Ana protection,Ana Clara Exemplo Souza,VAWA Self-Petition,Open\n"
    "105,00105-Asylum,Maria asylum,Maria Exemplo,Asylum,Open\n"
)
PDF = b"%PDF-1.4 made-up passport"


@pytest.fixture
def world(tmp_path):
    (tmp_path / "contacts.csv").write_text(CONTACTS, encoding="utf-8")
    (tmp_path / "matters.csv").write_text(MATTERS, encoding="utf-8")
    docs = tmp_path / "docs"
    (docs / "101").mkdir(parents=True)
    (docs / "101" / "passport.pdf").write_bytes(PDF)
    (docs / "105").mkdir()
    (docs / "105" / "i589.pdf").write_bytes(b"%PDF-1.4 i589")
    return {"contacts": tmp_path / "contacts.csv", "matters": tmp_path / "matters.csv", "docs": docs, "out": tmp_path / "clients",
            "portal": tmp_path / "portal", "cases": tmp_path / "cases", "root": tmp_path}


def go(w, **kw):
    return imp.run_import(w["contacts"], w["matters"], w["docs"], w["out"], w["portal"], cases=w["cases"], **kw)


def profile(w, client_id):
    return json.loads((w["portal"] / "clients" / client_id / "profile.json").read_text(encoding="utf-8"))


ANA, MARIA = "ana_clara_exemplo_souza-cl101", "maria_exemplo-cl102"


def test_a_clio_matter_becomes_a_case_named_as_a_connection_to_clio_names_it(world):
    from connectors.base import RemoteClient
    from connectors.sync import local_id

    run = go(world)
    made = {o.client_id: o.result for o in run.outcomes if o.client_id}
    assert made[ANA] == "created" and made[MARIA] == "created"
    # the same folder name a later connection to Clio gives the same matter: connecting afterwards finds these cases and adds nothing twice
    assert local_id(RemoteClient("101", "Ana Clara Exemplo Souza"), "clio") == ANA
    assert [p.name for p in (world["out"] / ANA / "source").glob("*.pdf")] == ["passport.pdf"]
    p = profile(world, ANA)
    assert p["imported_from"] == "clio" and p["clio_matter_id"] == "101" and p["clio_contact_id"] == "501" and p["clio_matter_type"] == "Immigration"
    assert p["name"] == "Ana Clara Exemplo Souza" and p["email"] == "ana.exemplo@example.com" and p["phone"] == "+1 617 555 0100"
    assert p["consent_asked"] is False and not any((p.get("consent") or {}).values())  # Clio carries no consent: nothing is sent
    assert (world["out"] / ANA / "clio_import.json").exists() and not (world["out"] / ANA / "docketwise_import.json").exists()
    import documents

    assert documents.source_of(ANA) == "clio"  # where its documents came from, on the Documents tab


def test_a_closed_matter_is_left_out_until_asked_for_and_the_words_say_clio(world):
    run = go(world)
    old = next(o for o in run.outcomes if o.matter_id == "103")
    assert old.result == "skipped" and old.why.startswith("closed in Clio") and not (world["out"] / "jean_egzanp-cl103").exists()
    again = go(world, include_archived=True)
    assert next(o for o in again.outcomes if o.matter_id == "103").result in ("created", "finished")


def test_a_vawa_or_asylum_matter_is_restricted_the_first_time_it_comes_in_and_never_invited(world):
    import restricted

    practice = imp.report(go(world, dry_run=True))
    assert "# Clio import: practice run, nothing was written" in practice and "Each would be restricted from the start" in practice and not world["cases"].exists()
    run = go(world)
    vawa, asylum = "ana_clara_exemplo_souza-cl104", "maria_exemplo-cl105"
    assert {o.client_id: o.protected for o in run.outcomes if o.protected} == {vawa: "1367", asylum: "208.6"}
    for cid, law in ((vawa, "1367"), (asylum, "208.6")):
        rec = restricted.record(world["cases"] / cid)
        assert rec["marked"]["on"] and rec["marked"]["law"] == law and rec["marked"]["by"] == "the Clio import"
        assert rec["marked"]["reason"].startswith("Imported from Clio, matter type ")
    assert not (world["cases"] / ANA / "access.json").exists()  # an immigration matter is not restricted by its practice area
    text = imp.report(run)
    section = text.split("## Restricted: protected cases, not invited", 1)[1].split("\n## ", 1)[0]
    assert "matter type VAWA Self-Petition, a VAWA, T or U visa case (8 U.S.C. 1367)" in section and "No invitation was sent" in section
    # the report names the practice areas as the matter types, and what it could and could not find in the export
    assert "- Asylum (1 matter): protected, an asylum case (8 CFR 208.6)" in text
    assert "Clio's help pages for its export could not be read" in text and "Docketwise" not in text


def test_an_export_with_no_matter_id_uses_the_matter_number_and_says_what_it_could_not_find(world):
    (world["root"] / "matters.csv").write_text("Matter Number,Description,Client,Practice Area,Status\n"
                                              "00101-Souza,Souza green card,Ana Clara Exemplo Souza,Immigration,Open\n", encoding="utf-8")
    run = go(world)
    [o] = [o for o in run.outcomes if o.client_id]
    assert o.result == "created" and o.client_id.startswith("ana_clara_exemplo_souza-cl") and "00101" not in o.client_id  # a short code, not the number
    text = imp.report(run)
    assert "Looked for and not in the file" in text and "column 'Matter Number'" in text
    (world["root"] / "matters.csv").write_text("Description,Client\nSouza,Ana\n", encoding="utf-8")
    with pytest.raises(imp.ImportProblem) as err:
        go(world)
    assert "Clio" in str(err.value) and "Docketwise" not in str(err.value)


def test_a_run_for_clio_leaves_the_docketwise_engine_as_it_was(world):
    assert engine.SRC["name"] == "Docketwise"
    go(world)
    assert engine.SRC["name"] == "Docketwise" and engine.STATE == "docketwise_import.json" and engine.local_id("Ana Souza", "4101").endswith("-dw4101")
    with pytest.raises(imp.ImportProblem):
        imp.run_import(world["contacts"], world["root"] / "missing.csv", None, world["out"], world["portal"], cases=world["cases"])
    assert engine.SRC["name"] == "Docketwise"  # also after a refusal


def test_the_command_line_says_clio_and_a_second_run_adds_nothing_twice(world, capsys):
    argv = ["--contacts", str(world["contacts"]), "--matters", str(world["matters"]), "--documents", str(world["docs"]), "--out", str(world["out"]),
            "--portal", str(world["portal"]), "--cases", str(world["cases"])]
    assert imp.main(argv + ["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Clio import" in out and "Practice run: nothing was written" in out and not world["out"].exists()
    assert imp.main(argv) == 0 and "The report is saved as" in capsys.readouterr().out
    before = sorted(p.name for p in world["out"].iterdir())
    assert imp.main(argv) == 0
    assert sorted(p.name for p in world["out"].iterdir() if not p.name.startswith("import_report")) == [n for n in before if not n.startswith("import_report")]
    assert "Clio import" in (world["out"] / "import_report.md").read_text(encoding="utf-8")


def test_every_column_name_says_where_it_comes_from_and_that_it_is_to_confirm():
    mapping = json.loads((schema_path.path("firm", "import_clio_columns")).read_text(encoding="utf-8"))
    assert "TO CONFIRM WITH THE FIRM'S EXPORT" in mapping["_status"] and "403" in mapping["_status"]
    for part in ("contacts", "matters"):
        for field_, basis in mapping[part]["_basis"].items():
            assert basis.startswith("guess") or ("Clio API" in basis and "https://" in basis), (part, field_)
        assert set(mapping[part]["_basis"]) <= set(mapping[part])
    for src in mapping["_sources"]:
        assert src["url"].startswith("https://") and src["read"] == "2026-10-03"


def test_the_register_has_the_columns_check_and_the_firms_own_pages_are_not_cited_as_read():
    items = {i["id"]: i for i in json.loads((schema_path.path("register", "maintenance")).read_text(encoding="utf-8"))["items"]}
    item = items["clio_export_columns"]
    assert item["party"] == "provider" and item["cadence"] == "quarterly" and item["last_checked"] is None and "403" in item["source"]
    for path in item["where"][:3]:
        assert (REPO / path.split(" ")[0]).exists()


def test_the_import_names_a_client_as_a_connection_to_clio_does_and_closed_is_never_consent(world):
    from connectors.base import RemoteClient
    from connectors.sync import local_id

    (world["root"] / "contacts.csv").write_text(
        "Contact ID,First Name,Middle Name,Last Name,Primary Email Address,Primary Phone Number,SMS OK\n"
        "501,Ana Clara,Maria,Exemplo Souza,ana.exemplo@example.com,+1 617 555 0100,closed\n", encoding="utf-8")
    (world["root"] / "matters.csv").write_text("Matter ID,Matter Number,Description,Client,Practice Area,Status\n"
                                              "101,00101-Souza,Souza green card,Ana Clara Exemplo Souza,Immigration,Open\n", encoding="utf-8")
    go(world)
    # the contact has a middle name; the connection names the case from Clio's client name, and so does the import: one case, not two
    assert local_id(RemoteClient("101", "Ana Clara Exemplo Souza"), "clio") == ANA and (world["out"] / ANA).is_dir()
    p = profile(world, ANA)
    assert p["clio_matter_id"] == "101" and p["consent_asked"] is False and not any((p.get("consent") or {}).values())  # "closed" in a consent cell is not a yes
    # the connection writes the same key for the same matter
    mapping = json.loads((schema_path.path("firm", "import_clio_columns")).read_text(encoding="utf-8"))
    assert "closed" not in mapping["yes"] and mapping["archived_yes"] == ["closed"]
