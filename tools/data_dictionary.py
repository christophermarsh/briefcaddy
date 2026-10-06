"""Writes docs/data_dictionary.md: every record the product keeps, with its fields, versions and where a person's data is.

    python tools/data_dictionary.py            write docs/data_dictionary.md
    python tools/data_dictionary.py --check    fail (exit 1) when the committed file differs from what the code says now

Nothing in this tool is a second copy of a field's meaning. The sentences are the code's own:
  - the JSON and JSON Lines records: src/records.py (the catalog the export of the firm's data also reads), and the ledger's kinds from src/events.py;
  - the SQLite tables: the comment beside each column in src/index.py and src/query.py (SCHEMA), and each table's sentence in their TABLES.
A test (tests/test_data_dictionary.py) regenerates the file and fails when it differs from the committed one, so adding a field or a table without
a sentence for it, or changing one without regenerating the dictionary, cannot pass. The output carries no date and no version number: it changes only
when a record does.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

OUT = REPO / "docs" / "data_dictionary.md"
AREAS = {"case": "A case's folder", "portal": "The client portal's folder for the client", "firm": "The firm's own files", "logs": "The logs"}
FLAG_WORDS = {"person": "Holds a person's data", "secret": "A secret: never exported"}


def cell(text: Any) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def sql_tables(schema: str) -> list[dict[str, Any]]:
    """The tables of a SCHEMA string: [{name, columns: [{name, type, notes, meaning}]}]. Each column's sentence is its comment."""
    tables: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in schema.splitlines():
        m = re.match(r"\s*create (?:virtual )?table if not exists (\w+)", line)
        if m:
            current = {"name": m.group(1), "columns": [], "virtual": "virtual" in line}
            tables.append(current)
            continue
        if current is None:
            continue
        if line.strip().startswith(");") or line.strip() == ")":
            current = None
            continue
        code, _, comment = line.partition("--")
        code = code.strip().rstrip(",").strip()
        if not code or re.match(r"(primary key|unique|tokenize)\b", code):
            continue
        parts = code.split()
        name, rest = parts[0], parts[1:]
        kind = rest[0] if rest and rest[0] in ("text", "integer", "real", "blob") else "text"
        notes = " ".join(w for w in (["primary key"] if "primary key" in code else []) + (["not null"] if "not null" in code else [])
                         + (["not searched"] if "unindexed" in rest else []))
        current["columns"].append({"name": name, "type": kind, "notes": notes, "meaning": comment.strip()})
    return tables


def render() -> str:
    import events
    import find
    import index
    import query
    import records

    out: list[str] = []
    add = out.append
    add("# The firm's data dictionary")
    add("")
    add("> Made from the code by `tools/data_dictionary.py`. Do not edit it by hand: change the sentence in the code and run the tool. A test fails when this file is out of date.")
    add("")
    add("Every record the product keeps, where it lives, who writes it, each field with its type and what it means, the record's version, and which fields hold a "
        "person's data. It is written for the firm's IT person, for the firm's attorney deciding what the firm may keep and hand over, and for anyone building a tool on the "
        "firm's own data. **All of it is plain files in ordinary formats (JSON, JSON Lines, PDF, SQLite). Nothing here needs our software to read.**")
    add("")
    add("## How to read it")
    add("")
    add("- **Where.** Paths are relative to the firm's data folder (`data/`). `<case id>` is the client's folder name.")
    add("- **Type.** `text`, `integer`, `number`, `boolean`, `list`, `object`, `any` (the type of the value, whatever it is), `lines` (a file of JSON Lines), `file`.")
    add("- **Version.** A record's version is the number in its own `version` field. Most records have none: they have had one shape so far, and the event ledger counts them as "
        "version 1. When a record gains a field the way an older record can still be read, the version is not raised and the section says what was added; when a record's "
        "shape changes so an older one cannot be read as it is, the version is raised and the section says what changed in each. The SQLite files are copies built from the "
        "records: when a table changes their schema version is raised, the old file is dropped, and it is built again.")
    add("- **Person's data.** A field marked *person* can hold a person's own data (a name, a date, a number, an answer, a document's text, a file name a client chose). "
        "The ledger and the query layer hold no fact's value, name, number read from a document, note or document text, but both carry the case id and some dates "
        "(their sections say exactly which); the one exception is the query layer's people table, the people index the conflict search reads, which holds every "
        "person's names, dates of birth and numbers on purpose. The export procedure (`docs/security/exit_procedure.md`) and the data statement (`docs/security/product_data_statement.md`) point here.")
    add("- **A case id is a client's folder name,** and the front desk, Docketwise and the connectors make that name from the client's name (`ana_clara_exemplo_souza-dw4101`). "
        "So a case id can be a person's name, and every field that holds one is marked *person*: the ledger's `case`, the view and access logs, `client_id`, and the `case_id` "
        "column of every SQL table.")
    add("- **Times.** A time with an offset (such as `T10:05:09-04:00`) is the firm's own time as the product's clock wrote it (src/clock.py: the office's time zone from Settings). "
        "A few records the portal and the staff accounts write themselves use UTC (`+00:00`). A time with no offset is UTC. The ledger's monthly files are named for the month "
        "the row's own time says, in the firm's time, so a row at 11:59 pm on the last day of the month is in that month's file.")
    add("- **Secret.** A field marked *secret* is never exported, and is not in any copy of the data a firm hands to anyone.")
    add("")
    add("## Where everything lives")
    add("")
    add("| Record | Where | Format |")
    add("|---|---|---|")
    for r in records.RECORDS:
        add(f"| {cell(r['title'])} | `{r['where']}` | {r['format']} |")
    for d in records.DATABASES:
        add(f"| {cell(d['title'])} | `{d['where']}` | SQLite database |")
    for w in records.WORKING_FILES:
        add(f"| {cell(w['title'])} | `{w['where']}` | {w['format']} |")
    add("")
    for r in records.RECORDS:
        add(f"## {r['title']}")
        add("")
        add(f"- **Where:** `{r['where']}`")
        add(f"- **Format:** {r['format']}")
        add(f"- **Who writes it:** {r['written_by']}")
        add(f"- **Version:** {'the `version` field, now ' + str(r['version']) if r['version'] else 'no version field (the ledger counts it as version 1)'}")
        if r["versions"]:
            add("- **What changed in each version:**")
            for n, what in r["versions"]:
                add(f"  - Version {n}: {what}")
        add(f"- **In an export of the firm's data:** {'yes' if r['exported'] else 'no'}")
        add("")
        add("| Field | Type | Meaning | |")
        add("|---|---|---|---|")
        for name, kind, meaning, flag in r["fields"]:
            add(f"| `{cell(name)}` | {kind} | {cell(meaning)} | {FLAG_WORDS.get(flag, '')} |")
        add("")
        if r["id"] == "events":
            add("The kinds a row can have (`kind`):")
            add("")
            add("| Kind | What it covers | About |")
            add("|---|---|---|")
            for key, k in events.KINDS.items():
                add(f"| `{key}` | {k['name']} | {'the firm' if k['firm'] else 'a case'} |")
            add("")
            add("The ledger's rows are in `data/events-YYYY-MM.jsonl`, one file a month, named by the month of the row's `at` (UTC). `I485_EVENTS` moves the ledger; the files "
                "are always beside the name it gives. The overnight run and every screen read it through an index built the way the view log's is; the query layer copies it into "
                "its `events` table.")
            add("")
    for d in records.DATABASES:
        module = {"index": index, "query": query, "find": find}[d["module"]]
        add(f"## {d['title']}")
        add("")
        add(f"- **Where:** `{d['where']}`")
        add("- **Format:** SQLite database")
        add(f"- **Who writes it:** {d['written_by']}")
        add(f"- **Version:** schema version {getattr(module, d['version_const'])} (kept in the file's `user_version`; when it is raised the file is dropped and built again)")
        add(f"- **In an export of the firm's data:** {'yes' if d['exported'] else 'no'}")
        add(f"- **Holds a person's data:** {'yes' if d['holds_person'] else 'no'}. {d['note']}")
        add("")
        for t in sql_tables(module.SCHEMA):
            add(f"### `{t['name']}`")
            add("")
            add(module.TABLES.get(t["name"], ""))
            add("")
            add("| Column | Type | Meaning | |")
            add("|---|---|---|---|")
            for c in t["columns"]:
                person = ("Holds a person's data (a case id can be a name)" if c["name"] == "case_id" else "Holds a person's name" if (c["name"], t["name"]) == ("name", "cases")
                          else "Holds a person's data" if (t["name"], c["name"]) in getattr(module, "PERSON_COLUMNS", ()) else "")
                add(f"| `{c['name']}` | {c['type']} | {cell(c['meaning'])} | {'; '.join(x for x in (c['notes'], person) if x)} |")
            add("")
        add("")
        if d["id"] == "query":
            add("Read it with any SQLite tool, read-only, for example:")
            add("")
            add("```")
            add('sqlite3 -readonly data/query.db "select stage, count(*) from cases group by stage"')
            add('sqlite3 -readonly data/query.db "select who, count(*) from events where at >= \'2026-10-01\' group by who"')
            add("```")
            add("")
    add("## Working files that hold a person's data")
    add("")
    add("Copies and work in progress, not records. Each is written for its owner only (permission 0600, and 0700 for the folder), is protected and backed up with the cases, "
        "and is never in an export of the firm's data.")
    add("")
    for w in records.WORKING_FILES:
        add(f"### {w['title']}")
        add("")
        add(f"- **Where:** `{w['where']}`")
        add(f"- **Format:** {w['format']}")
        add(f"- **Who writes it:** {w['written_by']}")
        add(f"- **What it holds:** {w['holds']}")
        add("- **In an export of the firm's data:** no")
        add("")
    add("## Where a person's data is")
    add("")
    add("The fields marked *person* above, gathered, so the data statement and the exit procedure can point at one list.")
    add("")
    add("| Record | Fields that can hold a person's data |")
    add("|---|---|")
    for r in records.RECORDS:
        names = [f"`{n}`" for n, _k, _m, flag in r["fields"] if flag == "person"]
        if names:
            add(f"| {cell(r['title'])} | {', '.join(names)} |")
    for d in records.DATABASES:
        add(f"| {cell(d['title'])} | {'Every table holds a person (a name, identifiers, document text).' if d['id'] == 'index' else 'The case id of every row (it can be a name), the dates of documents, deadlines and filings. The people table (the people index the conflict search reads): every person a case names, with their names, dates of birth, A-Numbers, passport numbers and countries. No other table holds a fact value, name, number read from a document, note or document text.'} |")
    for w in records.WORKING_FILES:
        add(f"| {cell(w['title'])} | {cell(w['holds'])} |")
    add("")
    add("## What an export of the firm's data leaves out")
    add("")
    add("| Left out | Why |")
    add("|---|---|")
    for what, why in records.NEVER_EXPORTED:
        add(f"| {cell(what)} | {cell(why)} |")
    add("")
    add("## What a backup leaves out")
    add("")
    add("Everything else in the data folder is in every backup, and the monthly restore drill compares each of those files with the live copy.")
    add("")
    add("| Left out | Why |")
    add("|---|---|")
    for what, why in records.NOT_BACKED_UP:
        add(f"| {cell(what)} | {cell(why)} |")
    add("")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write docs/data_dictionary.md from the code.")
    ap.add_argument("--check", action="store_true", help="fail when the committed dictionary differs from what the code says")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        have = OUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT.exists() else ""
        if have != text:
            print("docs/data_dictionary.md is out of date: run python tools/data_dictionary.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
