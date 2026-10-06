"""The export reads itself (src/export_reader.py: index.html at the root of every export; brief Q3). A made-up firm (tests/firm_world.py) is exported, its first page is read
the way a person without our software would (no script, nothing from the network, links into the same zip), and the catalog is changed to show the page follows it.
Everyone here is made up."""

from __future__ import annotations

import html
import json
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest

import events
import records

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_firm  # noqa: E402
import firm_world  # noqa: E402
import restricted  # noqa: E402
import version  # noqa: E402


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "data" / "events.jsonl"))
    for name in ("I485_SETTINGS", "I485_POLICIES_FIRM", "I485_RULES_APPROVED", "I485_MAINTENANCE_LOG"):
        monkeypatch.delenv(name, raising=False)
    f = firm_world.make_firm(tmp_path, cases=2)
    events.record("decisions", "confirmed", "Confirmed: applicant date of birth", case="ana-exemplo", who="Jane Paralegal", role="paralegal", version=1)
    events.record("settings", "changed", "Changed the office's address", who="Sam Attorney", role="attorney")
    return f


def export(firm) -> tuple[Path, dict[str, bytes]]:
    done = export_firm.everything(export_firm.default_where(firm["clients"], firm["portal"], firm["users"]), who="Sam Attorney", role="attorney", via="staff")
    with zipfile.ZipFile(done["path"]) as z:
        return Path(done["path"]), {n: z.read(n) for n in z.namelist()}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs, self.srcs, self.tags, self.scripts = [], [], [], 0

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        d = dict(attrs)
        if tag == "a" and d.get("href"):
            self.hrefs.append(d["href"])
        if d.get("src"):
            self.srcs.append(d["src"])
        if tag == "script":
            self.scripts += 1


def page_of(members: dict[str, bytes]) -> str:
    return members["index.html"].decode("utf-8")


def test_the_export_has_one_first_page_at_its_root_that_names_the_date_and_the_version(firm):
    _, members = export(firm)
    page = page_of(members)
    assert [n for n in members if "/" not in n and n.endswith(".html")] == ["index.html"]
    assert page.startswith("<!doctype html>") and "<title>The firm&#x27;s records, exported " in page
    manifest = json.loads(members["manifest.json"])
    assert any(f["path"] == "index.html" for f in manifest["files"])  # it is in the manifest like every other file
    from clock import us_date, today

    assert us_date(today().isoformat()) in page and f"version {version.VERSION} of the case system" in page
    assert "README.txt" in members and b"index.html" in members["README.txt"] and b"START HERE" in members["README.txt"]


def test_every_case_is_listed_with_the_clients_name_and_the_kind_of_case(firm):
    _, members = export(firm)
    page = page_of(members)
    cases = sorted({n.split("/")[1] for n in members if n.startswith("cases/")})
    assert cases == ["ana-exemplo", "ana-exemplo-1", "rosa-exemplo"] and f"Cases ({len(cases)})" in page
    for case in cases:
        assert f'id="case-{case}"' in page
    assert "<summary>Made Up Given_name Made Up Family_name <span" in page  # a name is the facts' name, in ordinary capitals (the made-up facts are "MADE UP GIVEN_NAME")
    assert "VAWA self-petition" in page and "Kind of case not worked out yet" in page  # rosa's kind was set by an attorney; the others have none yet


def test_a_cases_records_carry_their_catalog_title_and_schema_version_and_link_to_the_file(firm):
    _, members = export(firm)
    page = page_of(members)
    section = page.split('id="case-ana-exemplo"', 1)[1].split("</details>", 1)[0]
    assert "<td>Documents <a class=\"sub\" href=\"#r-documents\">its fields</a></td><td>version 1</td>" in section  # documents.json carries its own version
    assert "Fact graph" in section and "no version field (shape 1)" in section  # a record with none: the catalog's first shape
    assert 'href="cases/ana-exemplo/documents.json"' in section and 'href="cases/ana-exemplo/fact_graph.json"' in section
    for rel in ("fact_graph.json", "documents.json", "decisions.json"):
        assert f"cases/ana-exemplo/{rel}" in members  # a link that goes somewhere in the same zip
    facts = json.loads(members["cases/ana-exemplo/fact_graph.json"])["facts"]
    assert "MADE UP FAMILY_NAME" in section or any(str(f["value"]) in section for f in facts.values() if f.get("value"))  # the values are on the page too


def test_every_document_is_a_link_to_its_file_in_the_export(firm):
    _, members = export(firm)
    page = page_of(members)
    section = page.split('id="case-ana-exemplo"', 1)[1].split("</details>", 1)[0]
    assert '<a href="cases/ana-exemplo/source/passport-0.pdf">source/passport-0.pdf</a>' in section
    docs = [n for n in members if n.startswith("cases/") and n.lower().endswith(".pdf") and "/source/" in n or "/documents/" in n]
    assert docs and all(f'href="{n}"' in page for n in docs if n.count("/") == 3)


def test_every_relative_link_on_the_page_goes_to_a_file_in_the_zip(firm):
    _, members = export(firm)
    links = Links()
    links.feed(page_of(members))
    missing = [h for h in links.hrefs if not h.startswith(("#", "http")) and unquote(h.split("#")[0]) not in members]
    assert not missing, missing[:5]
    assert links.hrefs and any(h.startswith("ledger/events-") for h in links.hrefs)


def test_the_page_reads_with_scripts_off_and_needs_nothing_from_anywhere_else(firm):
    _, members = export(firm)
    page = page_of(members)
    links = Links()
    links.feed(page)
    assert links.scripts == 1 and not links.srcs and "link" not in links.tags and "img" not in links.tags and "iframe" not in links.tags
    assert not re.search(r"https?://", page) and "@import" not in page and "url(" not in page  # no outside resource of any kind
    bare = re.sub(r"<script>.*?</script>", "", page, flags=re.S)
    assert "Cases (3)" in bare and "ana-exemplo" in bare and "<details" in bare  # complete without the script (folding is the browser's own)
    for arc, data in members.items():
        if arc.startswith("ledger/") and arc.endswith(".html"):
            assert b"<script" not in data and b"http" not in data


def test_the_ledger_is_a_table_by_month_beside_its_file(firm):
    _, members = export(firm)
    page = page_of(members)
    months = sorted(n for n in members if n.startswith("ledger/events-") and n.endswith(".jsonl"))
    assert months
    for month in months:
        table = members[month[:-6] + ".html"].decode("utf-8")
        rows = [json.loads(x) for x in members[month].decode().splitlines() if x.strip()]
        assert "<table>" in table and table.count("<tr>") == len(rows) + 1 and f"{len(rows)} rows" in table
        assert all(html.escape(r["what"]) in table for r in rows)
        assert f'href="{month}"' in page and f'href="{month[:-6]}.html"' in page


def test_the_firms_own_records_and_what_is_not_in_the_export_are_on_the_page(firm):
    _, members = export(firm)
    page = page_of(members)
    firm_part = page.split('id="firm"', 1)[1].split('id="ledger"', 1)[0]
    for arc in ("firm/settings.json", "firm/review_users.json", "firm/maintenance_log.json", "firm/policies_firm.json", "logs/review_views.jsonl"):
        assert f'href="{arc}"' in firm_part, arc
    left_out = page.split('id="left-out"', 1)[1].split('id="records"', 1)[0]
    for what, why in records.NEVER_EXPORTED:
        assert what.replace("&", "&amp;").replace("'", "&#x27;") in left_out
    assert "index.db" in left_out and "query.db" in left_out and "find.db" in left_out and "Every key file and secret store" in left_out  # the rebuilt indexes and the vault's key


def test_a_restricted_case_says_so_and_who_may_open_it_and_still_shows_its_values_to_the_attorney(firm):
    case = firm["clients"] / "ana-exemplo-1"
    restricted.mark(case, True, "A VAWA matter: the client asked", "Sam Attorney", "attorney")
    restricted.name_person(case, "jane@firm.example", True, "Sam Attorney", "attorney", "Jane Paralegal")
    _, members = export(firm)
    page = page_of(members)
    mine = page.split('id="case-ana-exemplo-1"', 1)[1].split("</details>", 1)[0]
    assert '<span class="badge">restricted</span>' in page.split('id="case-ana-exemplo-1"', 1)[1].split("</details>", 1)[0]
    assert "marked restricted by Sam Attorney" in mine and "A VAWA matter: the client asked" in mine and "Who may open it: the attorneys and Jane Paralegal" in mine
    assert "MADE UP" in mine  # the attorney's page: the values are there
    rosa = page.split('id="case-rosa-exemplo"', 1)[1].split("</details>", 1)[0]
    assert "restricted by law" in rosa and "1367" in rosa
    plain = page.split('id="case-ana-exemplo"', 1)[1].split("</details>", 1)[0]
    assert "restricted" not in plain.split("<h3>")[0]


def test_a_name_with_markup_in_it_is_text_not_markup(firm):
    case = firm["clients"] / "ana-exemplo"
    graph = json.loads((case / "fact_graph.json").read_text(encoding="utf-8"))
    graph["facts"]["applicant.given_name"]["value"] = "<img src=x onerror=alert(1)>"
    (case / "fact_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    _, members = export(firm)
    links = Links()
    links.feed(page_of(members))
    assert "img" not in links.tags and "&lt;img src=x onerror=alert(1)&gt;" in page_of(members).replace("&#x27;", "'")


def test_the_page_follows_the_catalog_a_changed_record_shape_changes_the_page_with_no_change_to_its_code(firm, monkeypatch):
    _, before = export(firm)
    assert "A made-up record the catalog gained" not in page_of(before)
    new = {"id": "gained", "title": "A made-up record the catalog gained", "area": "case", "files": ["gained.json"], "where": "data/clients/<case id>/gained.json", "format": "JSON object",
           "written_by": "Nobody: a test", "version": 3, "versions": [(1, "First."), (3, "Third.")],
           "fields": [("colour", "text", "The colour of the made-up thing.", ""), ("owner", "text", "Whose it is.", "person")], "exported": True}
    monkeypatch.setattr(records, "RECORDS", records.RECORDS + [new])
    (firm["clients"] / "ana-exemplo" / "gained.json").write_text(json.dumps({"version": 2, "colour": "teal"}), encoding="utf-8")
    renamed = [dict(r, title="Facts, under a new name") if r["id"] == "facts" else r for r in records.RECORDS]
    monkeypatch.setattr(records, "RECORDS", renamed)
    _, after = export(firm)
    page = page_of(after)
    section = page.split('id="case-ana-exemplo"', 1)[1].split("</details>", 1)[0]
    assert "<td>A made-up record the catalog gained <a" in section and "version 2 (the catalog&#x27;s latest is 3)" in section and 'href="cases/ana-exemplo/gained.json"' in section
    assert "Facts, under a new name" in section and "Fact graph<" not in section
    glossary = page.split('id="records"', 1)[1]
    assert "The colour of the made-up thing." in glossary and "a person&#8217;s data" in glossary and "Version 3: Third." in glossary


def test_every_record_the_catalog_exports_is_described_on_the_page(firm):
    _, members = export(firm)
    glossary = page_of(members).split('id="records"', 1)[1]
    for r in records.RECORDS:
        assert f'id="r-{r["id"]}"' in glossary, r["id"]
        for name, _t, meaning, _f in r["fields"]:
            assert export_reader_esc(meaning) in glossary, (r["id"], name)


def export_reader_esc(text: str) -> str:
    import export_reader

    return export_reader.esc(text)


def test_the_zip_with_its_first_page_matches_its_own_manifest_and_a_changed_byte_is_named(firm, tmp_path):
    path, _ = export(firm)
    assert export_firm.verify_zip(path) == []
    copy = tmp_path / "copy.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(copy, "w") as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "cases/ana-exemplo/documents.json":
                data = data.replace(b"made-up", b"MADE-UP")
            if info.filename == "ledger/events-2099-01.html":
                continue
            out.writestr(info, data)
        out.writestr("stray.txt", b"not on the list")
    problems = export_firm.verify_zip(copy)
    assert any("cases/ana-exemplo/documents.json: does not match the manifest" in p for p in problems) and any("stray.txt: in the zip but not on the manifest" in p for p in problems)
    assert export_firm.main(["--verify", str(path)]) == 0 and export_firm.main(["--verify", str(copy)]) == 1
